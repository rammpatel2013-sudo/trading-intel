"""Build the daily-brief context dict from banked NAS data.

Orchestrates the existing read-only MCP tool functions (``get_gamma_history``,
``get_vix``, ``get_vix_options``, ``get_walls``, ``get_straddle``,
``get_research_watchlist``, ``get_research_note``) into one plain dict, then
hands it to ``daily_brief_render.render_html``. No new DB queries — everything
flows through the established data layer (rule 1 spirit). Descriptive regime
context only (FlashAlpha rule 4).
"""

from __future__ import annotations

import math
import re
from datetime import date, timedelta
from typing import Any

import pandas as pd
import structlog
from sqlalchemy.orm import Session

from trading_intel.config import Settings, get_settings
from trading_intel.dashboard.chart_data import load_ohlc
from trading_intel.mcp.extra_tools import (
    get_flow_scorecard,
    get_index_skew,
    get_iv_tenor,
    get_research_note,
    get_research_watchlist,
    get_straddle,
    get_vix,
    get_vix_options,
    get_walls,
)
from trading_intel.mcp.tools import get_gamma_history
from trading_intel.market.gex_transition import compute as _gex_compute
from trading_intel.api.market_read import build_market_read
from trading_intel.api.newsletter import build_newsletter_signals
from trading_intel.synthesis.daily_brief_render import render_html

log = structlog.get_logger(__name__)

_INDEX_ROOTS = ("SPX", "SPY", "QQQ")
_DOC_ROOT = "SPX"
_BOARD_DAYS = 14
_SPARK_POINTS = 10
_SQRT_252 = 15.8745
_TICKER_RE = re.compile(r"^[A-Z]{1,5}$")
# Obvious non-single-name tokens the LLM extractor sometimes emits.
_JUNK = {"SPX", "SPXW", "NVDIA", "GOOG", "BRKB"}

# Expected-move RAILS anchored at each period's open (widest→narrowest).
# (label, vix_data IV field matched to the horizon, horizon in trading days).
# Q/M/W rails are FIXED at the period-open spot × that period's implied move;
# only Daily re-anchors each session, so you read today's price against static
# weekly/monthly/quarterly rails. SPX levels use SPY closes ×10 (SPX ≈ SPY×10;
# SPY is the maintained daily-quote series — SPX quotes_daily goes stale).
_EM_PERIODS = (
    ("Quarterly", "vix3m", 63),
    ("Monthly", "vix", 21),
    ("Weekly", "vix9d", 5),
    ("Daily", "vix9d", 1),
)
_IV_LABEL = {"vix9d": "VIX9D", "vix": "VIX", "vix3m": "VIX3M"}
_SPX_FROM_SPY = 10.0


def _latest(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    return rows[-1] if rows else None


def _index_block(session: Session, symbol: str) -> dict[str, Any] | None:
    hist = get_gamma_history(session, symbol, days=_BOARD_DAYS)
    rows = hist.get("rows") or []
    last = _latest(rows)
    if last is None:
        return {"symbol": symbol, "spot": None, "flip": None, "regime": None,
                "flip_series": [], "gex_series": [], "asof": None, "spot_vs_flip_pct": None}
    spot, flip = last.get("spot"), last.get("gex_flip")
    vf = ((spot - flip) / flip * 100.0) if (spot and flip) else None
    tail = rows[-_SPARK_POINTS:]
    return {
        "symbol": symbol,
        "spot": spot,
        "flip": flip,
        "regime": last.get("regime"),
        "spot_vs_flip_pct": vf,
        "flip_series": [r.get("gex_flip") for r in tail],
        "gex_series": [r.get("gex_total") for r in tail],
        "asof": last.get("date"),
    }


def _vix_block(session: Session) -> dict[str, Any]:
    vix = get_vix(session, days=40)
    summ = vix.get("summary") or {}
    series = vix.get("rows") or []
    last = series[-1] if series else {}
    floor = call_wall = call_oi_share = None
    opts = get_vix_options(session)
    if opts.get("found"):
        call_oi_share = opts.get("call_oi_share")
        calls: dict[float, float] = {}
        puts: dict[float, float] = {}
        for r in opts.get("rows") or []:
            k, oi, kind = r.get("strike"), r.get("oi") or 0, (r.get("kind") or "").lower()
            if k is None:
                continue
            (calls if kind.startswith("c") else puts)[k] = (
                (calls if kind.startswith("c") else puts).get(k, 0) + oi
            )
        if puts:
            floor = max(puts, key=lambda s: puts[s])
        if calls:
            call_wall = max(calls, key=lambda s: calls[s])
    return {
        "vix": summ.get("vix") or last.get("vix"),
        "vvix": last.get("vvix"),
        "vix9d": last.get("vix9d"),
        "vix3m": last.get("vix3m"),
        "term": summ.get("term_9d_3m"),
        "vrp": last.get("vrp"),
        "vega_zone": summ.get("vega_zone") or last.get("vega_zone"),
        "floor": floor,
        "call_wall": call_wall,
        "call_oi_share": call_oi_share,
        "asof": last.get("date"),
    }


def _doc_block(session: Session, doc_index: dict[str, Any] | None, vix_level: float | None) -> dict[str, Any]:
    walls = get_walls(session, _DOC_ROOT, dte_max=60)
    strad = get_straddle(session, _DOC_ROOT, dte_max=7)
    flip = (doc_index or {}).get("flip")
    spot = (doc_index or {}).get("spot") or walls.get("spot")
    regime = (doc_index or {}).get("regime") or ""
    below = spot is not None and flip is not None and spot < flip
    em_lo = strad.get("lower") if strad.get("found") else None
    em_hi = strad.get("upper") if strad.get("found") else None
    r16_lo = r16_hi = None
    if spot and vix_level:
        mv = spot * (vix_level / 100.0) / _SQRT_252
        r16_lo, r16_hi = spot - mv, spot + mv
    # Doc's stored daily-plan body (letter-body storage) if present, else a
    # data-driven reconstruction from flip + regime.
    note = get_research_note(session, "__DOC__")
    if note.get("found") and note.get("note_md"):
        expectation = note["note_md"][:600]
        exp_src = f"Doc letter {note.get('as_of') or ''}".strip()
    else:
        pos = "below" if below else "above"
        air = (
            "the air below is the live risk unless price reclaims the flip"
            if below
            else "acceptance above the flip keeps dealers damping"
        )
        expectation = (
            f"Spot {spot:,.0f} sits {pos} the {flip:,.0f} zero-gamma flip ({regime}). "
            f"With the flip {pos == 'below' and 'overhead' or 'beneath'}, {air}. "
            "Sell front vol / fade extremes while pinned; the flip is the tell."
            if (spot and flip)
            else "Flip/spot unavailable — Doc read pending."
        )
        exp_src = "reconstructed from flip + regime"
    return {
        "flip": flip,
        "spot": spot,
        "below": below,
        "call_wall": walls.get("call_wall"),
        "put_wall": walls.get("put_wall"),
        "em_lo": em_lo,
        "em_hi": em_hi,
        "r16_lo": r16_lo,
        "r16_hi": r16_hi,
        "walls_stale": bool(walls.get("as_of")),
        "expectation": expectation,
        "expectation_src": exp_src,
    }


def _is_clean_ticker(sym: str | None) -> bool:
    if not sym:
        return False
    s = sym.strip().upper()
    return bool(_TICKER_RE.match(s)) and s not in _JUNK


def _learned_block(session: Session) -> tuple[list[dict[str, Any]], int]:
    wl = get_research_watchlist(session, active_only=True, limit=200)
    rows = wl.get("rows") or []
    total = len(rows)
    seen: set[str] = set()
    clean: list[dict[str, Any]] = []
    for r in rows:
        sym = (r.get("symbol") or "").strip().upper()
        if not _is_clean_ticker(sym) or sym in seen:
            continue
        seen.add(sym)
        clean.append(
            {"symbol": sym, "themes": r.get("themes") or [], "sentiment": r.get("sentiment"),
             "rationale": r.get("rationale")}
        )
        if len(clean) >= 8:
            break
    return clean, total


def _tracker_block(session: Session) -> list[dict[str, Any]]:
    """High-conviction ideas surfaced from the trade-idea letters (data-driven).

    Structured trade parsing (exact strikes/structures) is a follow-up that needs
    letter-body extraction; for now this surfaces the strongest source-tagged names.
    """
    wl = get_research_watchlist(session, active_only=True, limit=200)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in wl.get("rows") or []:
        sym = (r.get("symbol") or "").strip().upper()
        sent, conf = r.get("sentiment"), r.get("confidence")
        if not _is_clean_ticker(sym) or sym in seen or sent is None or conf is None:
            continue
        if abs(sent) < 0.8 or conf < 0.8:
            continue
        seen.add(sym)
        out.append(
            {"src": "letters", "ticker": sym, "dir": "Bull" if sent > 0 else "Bear",
             "note": (r.get("rationale") or "")[:90], "status": "surfaced"}
        )
        if len(out) >= 8:
            break
    return out


def _through_line(indices: list[dict[str, Any]], vix: dict[str, Any]) -> str:
    below = [ix["symbol"] for ix in indices if (ix.get("spot_vs_flip_pct") or 0) < 0]
    above = [ix["symbol"] for ix in indices if (ix.get("spot_vs_flip_pct") or 0) > 0]
    parts: list[str] = []
    if below:
        parts.append(f"{', '.join(below)} sit below their gamma flip (short-γ, move-amplifying)")
    if above:
        parts.append(f"{', '.join(above)} hold above theirs (long-γ, damping)")
    vvix = vix.get("vvix")
    tail = ""
    if vvix and vvix > 95:
        tail = f" VVIX {vvix:.0f} says the tape is paying up for tail hedges."
    return (("; ".join(parts) + ".") if parts else "Mixed index gamma.") + tail


def _crosschecks(indices: list[dict[str, Any]], vix: dict[str, Any]) -> list[dict[str, Any]]:
    below = [ix["symbol"] for ix in indices if (ix.get("spot_vs_flip_pct") or 0) < 0]
    out = [{
        "claim": "Dealers short-gamma / move-amplifying",
        "source": "Doc / L&S",
        "our": f"{', '.join(below)} below flip" if below else "all indices above flip",
        "verdict": "✅ confirmed" if below else "◻︎ not today",
        "cls": "ok" if below else "dim",
    }]
    vvix = vix.get("vvix")
    if vvix is not None:
        out.append({
            "claim": "Tail-hedge bid into events",
            "source": "Doc",
            "our": f"VVIX {vvix:.0f}, VIX call-wall {vix.get('call_wall') or '—'}",
            "verdict": "✅ confirmed" if vvix > 95 else "⚠️ muted",
            "cls": "ok" if vvix > 95 else "warn",
        })
    return out


_LETTER_SOURCES = (
    ("__DOC__", "Doc McGraw"),
    ("__LONGSHORT__", "The Long & Short"),
    ("__JAGUAR__", "Jaguar Analytics"),
    ("__SITS__", "Special Situations"),
)


def _letters_block(session: Session) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key, label in _LETTER_SOURCES:
        note = get_research_note(session, key)
        if note.get("found") and note.get("note_md"):
            out.append({"src": label, "text": note["note_md"][:320]})
    return out


def _period_boundaries(today: date) -> dict[str, date]:
    """First calendar day of the current quarter / month / week (Mon) / day."""
    q_month = ((today.month - 1) // 3) * 3 + 1
    return {
        "Quarterly": date(today.year, q_month, 1),
        "Monthly": date(today.year, today.month, 1),
        "Weekly": today - timedelta(days=today.weekday()),
        "Daily": today,
    }


def _as_date(value: object) -> date | None:
    if value is None:
        return None
    try:
        return pd.Timestamp(value).date()
    except (ValueError, TypeError):
        return None


def _spy_closes(session: Session) -> list[tuple[date, float]]:
    """Ascending (date, close) for SPY — the maintained daily-quote series."""
    ohlc = load_ohlc(session, "SPY", days=160)
    if ohlc is None or ohlc.empty:
        return []
    out: list[tuple[date, float]] = []
    for _, r in ohlc.iterrows():
        d = _as_date(r.get("date"))
        c = r.get("close")
        if d is not None and c is not None:
            out.append((d, float(c)))
    out.sort(key=lambda t: t[0])
    return out


def _anchor_on_or_after(series: list[tuple[date, float]], boundary: date) -> tuple[date, float] | None:
    """First (date, close) at/after the period boundary (its opening print)."""
    for d, v in series:
        if d >= boundary:
            return (d, v)
    return series[-1] if series else None


def _em_levels_block(session: Session) -> dict[str, Any] | None:
    """SPX expected-move RAILS anchored at each period's open (fixed) + where
    price sits now. Q/M/W rails don't move within the period; Daily re-anchors
    each session, so today's price reads against static weekly/monthly/quarterly
    rails. SPX ≈ SPY×10 (SPY is the maintained daily series). Rule 4.
    """
    closes = _spy_closes(session)
    if len(closes) < 2:
        return None
    vrows = get_vix(session, days=160).get("rows") or []
    vmap = {r["date"]: r for r in vrows if r.get("date")}
    vdates = sorted(vmap)

    cur_date, cur_spy = closes[-1]
    cur_spx = cur_spy * _SPX_FROM_SPY
    bounds = _period_boundaries(cur_date)

    def _iv_on_or_after(boundary: date, key: str) -> float | None:
        biso = boundary.isoformat()
        for ds in vdates:
            if ds >= biso and vmap[ds].get(key) is not None:
                return vmap[ds][key]
        return vmap[vdates[-1]].get(key) if vdates else None

    out: list[dict[str, Any]] = []
    for label, key, n in _EM_PERIODS:
        if label == "Daily":
            anc_date, anc_spy = cur_date, cur_spy
            iv = _iv_on_or_after(cur_date, key)
        else:
            anc = _anchor_on_or_after(closes, bounds[label])
            if anc is None:
                continue
            anc_date, anc_spy = anc
            iv = _iv_on_or_after(anc_date, key)
        if iv is None:
            continue
        anc_spx = anc_spy * _SPX_FROM_SPY
        em_pct = iv * math.sqrt(n / 252.0)  # iv in vol points -> em_pct in %
        upper = anc_spx * (1 + em_pct / 100.0)
        lower = anc_spx * (1 - em_pct / 100.0)
        width = upper - lower
        pos = ((cur_spx - lower) / width * 100.0) if width > 0 else 50.0
        if cur_spx > upper:
            status = "▲ broke above (expansion)"
        elif cur_spx < lower:
            status = "▼ broke below (expansion)"
        elif pos >= 80:
            status = "near upper rail"
        elif pos <= 20:
            status = "near lower rail"
        else:
            status = "mid-range (balanced)"
        out.append(
            {
                "tenor": label,
                "iv_label": _IV_LABEL.get(key, key.upper()),
                "anchor_date": anc_date.isoformat(),
                "anchor_spot": anc_spx,
                "em_pct": em_pct,
                "upper": upper,
                "lower": lower,
                "pos_pct": max(0.0, min(100.0, pos)),
                "status": status,
            }
        )
    if not out:
        return None
    return {
        "current_spot": cur_spx,
        "current_src": "SPY×10",
        "as_of": cur_date.isoformat(),
        "rows": out,
    }


_MAG7 = ("AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA")


def _mag7_block(session: Session) -> list[dict[str, Any]]:
    """Mag7 gamma/vol snapshot — the mega-caps that drive the index."""
    out: list[dict[str, Any]] = []
    for sym in _MAG7:
        rows = get_gamma_history(session, sym, days=3).get("rows") or []
        last = rows[-1] if rows else None
        if not last:
            out.append({"symbol": sym, "found": False})
            continue
        spot, flip = last.get("spot"), last.get("gex_flip")
        out.append(
            {
                "symbol": sym,
                "spot": spot,
                "flip": flip,
                "vs_flip": ((spot - flip) / flip * 100.0) if (spot and flip) else None,
                "gex": last.get("gex_total"),
                "regime": last.get("regime"),
                "atm_iv": last.get("atm_iv"),
                "found": True,
            }
        )
    return out


def _flows_block(session: Session) -> list[dict[str, Any]]:
    """Top single-name option-flow names by notional (our own tape roll-up)."""
    sc = get_flow_scorecard(session, lookback_days=5, min_notional=1_000_000.0, limit=40)
    rows = sorted(
        sc.get("rows") or [], key=lambda r: (r.get("total_notional") or 0.0), reverse=True
    )[:5]
    return [
        {
            "root": r.get("root"),
            "notional": r.get("total_notional"),
            "net_delta": r.get("net_dollar_delta"),
            "label": r.get("label"),
            "score": r.get("accum_score"),
        }
        for r in rows
    ]


def _recap_block(
    indices: list[dict[str, Any]],
    em_levels: dict[str, Any] | None,
    vix: dict[str, Any],
    doc: dict[str, Any],
) -> dict[str, Any]:
    """Yesterday-vs-today: a data recap of our own tape + the desk 'what to expect'."""
    facts: list[str] = []
    spy = next((ix for ix in indices if ix.get("symbol") == "SPY"), None)
    if spy and spy.get("spot_vs_flip_pct") is not None and spy.get("flip"):
        side = "above" if spy["spot_vs_flip_pct"] > 0 else "below"
        facts.append(f"SPY last {spy.get('spot')} — {side} its {spy['flip']:.0f} gamma flip")
    if em_levels:
        wk = next((r for r in (em_levels.get("rows") or []) if r.get("tenor") == "Weekly"), None)
        if wk and wk.get("lower") and wk.get("upper"):
            facts.append(f"{wk.get('status')} of the weekly rail ({wk['lower']:.0f}–{wk['upper']:.0f})")
    if vix.get("vix") is not None:
        facts.append(f"VIX {vix.get('vix')} / VVIX {vix.get('vvix')}")
    return {
        "recap": ("; ".join(facts) + ".") if facts else "",
        "outlook": (doc.get("expectation") or "")[:400],
        "outlook_src": doc.get("expectation_src"),
    }


def _gex_transition_block(session: Session) -> dict[str, Any] | None:
    """SPX dealer-gamma "quiet unwind" state for the Doc section (best-effort).

    Reads net GEX (EOD) + CLEAN ATM IV (``iv_tenor``) and classifies today's
    state via the pure ``market.gex_transition`` machine. The edge is taken as
    given; this only surfaces the state. Descriptor only (FlashAlpha rule 4).
    """
    try:
        gamma = get_gamma_history(session, _DOC_ROOT, days=120).get("rows") or []
        iv = get_iv_tenor(session, symbols=[_DOC_ROOT], tenor_dte=30, days=120).get("rows") or []
        if not gamma:
            return None
        res = _gex_compute(gamma, iv, tenor_dte=30)
        cur = res.latest
        if cur is None:
            return None
        rows = [r for r in res.rows if r.net_gex is not None][-10:]
        over = ((cur.spot / cur.flip - 1.0) * 100.0) if (cur.spot and cur.flip) else None
        return {
            "state": cur.state,
            "net_gex": cur.net_gex,
            "d_gex_z": cur.d_gex_z,
            "d_iv_pt": cur.d_iv_pt,
            "atm_iv": cur.atm_iv,
            "flip": cur.flip,
            "spot": cur.spot,
            "over_pct": over,
            "firing": cur.state in ("quiet_unwind", "confirmed", "gex_drop"),
            "strip": [
                {"d": r.date.strftime("%m-%d"), "gex": r.net_gex, "z": r.d_gex_z, "state": r.state}
                for r in rows
            ],
        }
    except Exception:  # noqa: BLE001 — best-effort; brief renders without it
        log.warning("daily_brief.gex_transition_failed", exc_info=True)
        return None


def _vol_skew_block(session: Session) -> dict[str, Any] | None:
    """Compact skew / dispersion reads for the vol section (best-effort)."""
    try:
        rows = get_index_skew(session, days=30).get("rows") or []
        if not rows:
            return None

        def _last(key: str) -> Any:
            for r in reversed(rows):
                if r.get(key) is not None:
                    return r.get(key)
            return None

        return {
            "rr_pctile": _last("spx_rr_pctile_252d"),
            "sdex_pctile": _last("sdex_pctile_252d"),
            "cor1m": _last("cor1m"),
            "cor1m_pctile": _last("cor1m_pctile_252d"),
            "vvix_vix": _last("vvix_vix_ratio"),
            "dspx": _last("dspx"),
        }
    except Exception:  # noqa: BLE001
        log.warning("daily_brief.vol_skew_failed", exc_info=True)
        return None


def build_brief_context(session: Session, settings: Settings | None = None) -> dict[str, Any]:
    """Assemble the full daily-brief context dict from banked data."""
    settings = settings or get_settings()
    indices = [b for s in _INDEX_ROOTS if (b := _index_block(session, s)) is not None]
    vix = _vix_block(session)
    doc_index = next((ix for ix in indices if ix["symbol"] == _DOC_ROOT), None)
    doc = _doc_block(session, doc_index, vix.get("vix"))
    em_levels = _em_levels_block(session)
    recap = _recap_block(indices, em_levels, vix, doc)
    mag7 = _mag7_block(session)
    flows = _flows_block(session)
    learned, learned_total = _learned_block(session)
    try:
        market_read = build_market_read(session, symbol="SPX")
    except Exception:  # noqa: BLE001 — synthesis is best-effort; brief renders without it
        market_read = None
    try:
        newsletter = build_newsletter_signals(session)
    except Exception:  # noqa: BLE001
        newsletter = None
    ctx: dict[str, Any] = {
        "as_of": date.today().isoformat(),
        "subtitle": "pre-open daily brief · synthesis read, index gamma, Doc levels, letters",
        "through_line": _through_line(indices, vix),
        "market_read": market_read,
        "newsletter": newsletter,
        "recap": recap,
        "indices": indices,
        "mag7": mag7,
        "flows": flows,
        "vix": vix,
        "doc": doc,
        "gex_transition": _gex_transition_block(session),
        "vol_skew": _vol_skew_block(session),
        "em_levels": em_levels,
        "letters": _letters_block(session),
        "fresh_tags": None,
        "tracker": _tracker_block(session),
        "learned": learned,
        "learned_total": learned_total,
        "crosschecks": _crosschecks(indices, vix),
        "board_note": (
            "Flip trend and net-GEX sparklines run left→right over the last "
            f"{_SPARK_POINTS} sessions. Spot-vs-flip colored green (long γ) / red (short γ)."
        ),
        "vol_note": "VRP positive = implied rich vs realized; elevated VVIX = paying up for the vol path.",
        "provenance": "index γ + VIX + walls/straddle via trading-intel NAS · letters ingest (research watchlist).",
    }
    return ctx


def build_brief_html(session: Session, settings: Settings | None = None) -> tuple[str, dict[str, Any]]:
    """Return (html, context) for the daily brief."""
    ctx = build_brief_context(session, settings)
    return render_html(ctx), ctx
