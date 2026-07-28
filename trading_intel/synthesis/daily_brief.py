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
from datetime import date
from typing import Any

import structlog
from sqlalchemy.orm import Session

from trading_intel.config import Settings, get_settings
from trading_intel.mcp.extra_tools import (
    get_research_note,
    get_research_watchlist,
    get_straddle,
    get_vix,
    get_vix_options,
    get_walls,
)
from trading_intel.mcp.tools import get_gamma_history
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
    # Data-driven descriptive read (verbatim Doc prose swaps in once the letter body is stored).
    note = get_research_note(session, _DOC_ROOT)
    if note.get("found") and note.get("note_md"):
        expectation = note["note_md"][:600]
        exp_src = f"Doc note {note.get('as_of') or ''}".strip()
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


def _letters_block(session: Session) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for sym, label in (("SPX", "Doc McGraw"), ("MKT", "The Long & Short"), ("SITS", "Special Situations")):
        note = get_research_note(session, sym)
        if note.get("found") and note.get("note_md"):
            out.append({"src": label, "text": note["note_md"][:280]})
    return out


def build_brief_context(session: Session, settings: Settings | None = None) -> dict[str, Any]:
    """Assemble the full daily-brief context dict from banked data."""
    settings = settings or get_settings()
    indices = [b for s in _INDEX_ROOTS if (b := _index_block(session, s)) is not None]
    vix = _vix_block(session)
    doc_index = next((ix for ix in indices if ix["symbol"] == _DOC_ROOT), None)
    doc = _doc_block(session, doc_index, vix.get("vix"))
    learned, learned_total = _learned_block(session)
    ctx: dict[str, Any] = {
        "as_of": date.today().isoformat(),
        "subtitle": "pre-open daily brief · index gamma, Doc levels, letters",
        "through_line": _through_line(indices, vix),
        "indices": indices,
        "vix": vix,
        "doc": doc,
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
