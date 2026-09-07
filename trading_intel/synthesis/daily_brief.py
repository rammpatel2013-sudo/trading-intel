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
from sqlalchemy import text
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
from trading_intel.letters.clean import clean_body, snippet
from trading_intel.synthesis.daily_brief_render import render_html
from trading_intel.vol.vix_calendar import is_market_holiday

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

#: A stored letter older than this many TRADING days no longer speaks for today:
#: it is still quoted (with its age) but its levels and scenarios are withheld
#: from the triggers/levels blocks so a stale number can never drive a read.
_STALE_LETTER_DAYS = 3


def _trading_sessions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse raw snapshot rows to ONE row per real trading session, ascending.

    Two things made the board wrong before this existed. ``get_gamma_history``
    returns one row per collector snapshot, so a single date can carry three or
    four rows — ``rows[-10:]`` labelled "the last 10 sessions" was really about
    three days of intraday noise. And ``greeks_snapshot`` runs seven days a week:
    on a weekend or holiday spot is frozen at the prior close while the greeks
    are re-derived from a stale chain, so ``rows[-1]`` could report a Sunday, and
    the drift between two closed days read as a real move in net GEX.

    Keeps the LAST snapshot of each open session (the EOD read) and drops closed
    days entirely.
    """
    by_day: dict[date, dict[str, Any]] = {}
    for r in rows or []:
        d = _as_date(r.get("date") or r.get("ts"))
        if d is None or is_market_holiday(d):
            continue
        by_day[d] = {**r, "session": d}
    return [by_day[d] for d in sorted(by_day)]


def _trading_days_between(start: date, end: date) -> int:
    """Count of open sessions strictly after ``start``, up to and including ``end``."""
    if end <= start:
        return 0
    n, d = 0, start
    while d < end:
        d += timedelta(days=1)
        if not is_market_holiday(d):
            n += 1
    return n


def _prev_session(d: date) -> date:
    """The most recent open session at or before ``d``."""
    while is_market_holiday(d):
        d -= timedelta(days=1)
    return d


def _latest(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    return rows[-1] if rows else None


def _index_block(session: Session, symbol: str) -> dict[str, Any] | None:
    hist = get_gamma_history(session, symbol, days=_BOARD_DAYS)
    rows = _trading_sessions(hist.get("rows") or [])
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
        "asof": (last.get("session") or _as_date(last.get("date")) or date.today()).isoformat(),
        "sessions": len(rows),
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


def _letter_age(note: dict[str, Any], today: date) -> dict[str, Any]:
    """Age a stored letter in trading days and decide whether it still speaks.

    ``get_research_note`` hands back the newest note for a source with no notion
    of whether "newest" is recent. On 2026-09-07 the newest ``__DOC__`` note was
    an Aug 13 daily plan and the brief printed its levels under "Doc's read into
    today". Everything downstream now goes through this: ``fresh`` gates the
    levels and scenarios, ``label`` is what the reader sees.
    """
    as_of = _as_date(note.get("as_of"))
    if as_of is None:
        return {"as_of": None, "age": None, "fresh": False, "label": "undated"}
    age = _trading_days_between(as_of, _prev_session(today))
    return {
        "as_of": as_of.isoformat(),
        "age": age,
        "fresh": age <= _STALE_LETTER_DAYS,
        "label": (
            "today's letter" if age == 0
            else f"{age} session{'s' if age != 1 else ''} old"
        ),
    }


def _doc_block(
    session: Session,
    doc_index: dict[str, Any] | None,
    vix_level: float | None,
    today: date | None = None,
) -> dict[str, Any]:
    today = today or date.today()
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

    # Walls: report REAL staleness. This used to be ``bool(walls.get("as_of"))``,
    # which is true whenever a snapshot exists at all — so the "wall levels are
    # from the last stored index chain" caveat printed on every single run,
    # including runs where the chain was collected that morning. A caveat that is
    # always on is a caveat nobody reads.
    walls_as_of = _as_date(walls.get("as_of"))
    walls_age = (
        _trading_days_between(walls_as_of, _prev_session(today))
        if walls_as_of else None
    )
    walls_stale = walls_age is None or walls_age > 1

    # Doc's stored daily-plan body, but only where it is still current.
    note = get_research_note(session, "__DOC__")
    age = _letter_age(note, today) if note.get("found") else {
        "as_of": None, "age": None, "fresh": False, "label": "no letter stored"
    }
    body = clean_body(note.get("note_md")) if note.get("found") else ""
    if body and age["fresh"]:
        expectation = snippet(body, 600)
        exp_src = f"Doc letter {age['as_of']} · {age['label']}"
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
        exp_src = "our data — no current Doc letter"
    return {
        "flip": flip,
        "spot": spot,
        "below": below,
        "call_wall": walls.get("call_wall"),
        "put_wall": walls.get("put_wall"),
        "walls_as_of": walls_as_of.isoformat() if walls_as_of else None,
        "em_lo": em_lo,
        "em_hi": em_hi,
        "r16_lo": r16_lo,
        "r16_hi": r16_hi,
        "walls_stale": walls_stale,
        "expectation": expectation,
        "expectation_src": exp_src,
        "letter_age": age,
        # The stale body is still worth READING — it just must not be presented
        # as today's plan, and its levels stay out of the triggers block.
        "stale_quote": snippet(body, 420) if (body and not age["fresh"]) else "",
    }


def _is_clean_ticker(sym: str | None) -> bool:
    if not sym:
        return False
    s = sym.strip().upper()
    return bool(_TICKER_RE.match(s)) and s not in _JUNK


def _tradeable(session: Session) -> set[str]:
    """Symbols we actually hold market data for — the optionability gate.

    The letter extractor emits anything ticker-SHAPED, so policy and macro
    acronyms ride onto the watchlist and then into the brief: the 2026-09-07
    edition published ``USMCA`` as a bull idea. A real name has hundreds of
    greeks/quote rows; ``USMCA`` has zero of each. One existence query settles
    it without a hand-maintained stoplist.
    """
    try:
        rows = session.execute(
            text(
                "SELECT symbol FROM greeks_snapshots "
                "UNION SELECT symbol FROM quotes_daily"
            )
        ).scalars().all()
        return {str(r).strip().upper() for r in rows}
    except Exception:  # noqa: BLE001 — a failed gate must not empty the section
        log.warning("daily_brief.tradeable_gate_failed", exc_info=True)
        return set()


#: A rationale describing a BASKET or theme is not a single-name call. AAPL
#: shipped Bull on "Industrial and Auto Analog Recovery basket includes ...".
_THEME_WORDS = ("basket", "screen", "universe", "complex", "cohort",
                "these names", "the group", "sector rotation")

#: A rationale that only records THAT a letter named the ticker, not what it
#: argued. These are worth surfacing but must not sit beside a real thesis
#: wearing the same "Bull"/"Bear" badge: "The document mentions $WMT as part of
#: the BTO Thursday morning trade" is not a call on Walmart, and "OPEX is
#: scheduled for Friday trading" is about expiration, not the ticker OPEX.
_MENTION_ONLY = ("document mentions", "is mentioned", "mentioned as", "mentioned in",
                 "often referenced", "primary focus of the document",
                 "discussed in detail", "is scheduled for", "as part of the",
                 "the document")


def _is_mention_only(rationale: str) -> bool:
    low = (rationale or "").lower()
    return any(w in low for w in _MENTION_ONLY)


def _learned_block(session: Session) -> tuple[list[dict[str, Any]], int]:
    wl = get_research_watchlist(session, active_only=True, limit=200)
    rows = wl.get("rows") or []
    total = len(rows)
    tradeable = _tradeable(session)
    seen: set[str] = set()
    clean: list[dict[str, Any]] = []
    for r in rows:
        sym = (r.get("symbol") or "").strip().upper()
        if not _is_clean_ticker(sym) or sym in seen:
            continue
        if tradeable and sym not in tradeable:
            continue
        seen.add(sym)
        clean.append(
            {"symbol": sym, "themes": r.get("themes") or [], "sentiment": r.get("sentiment"),
             "rationale": r.get("rationale")}
        )
        if len(clean) >= 8:
            break
    return clean, total


#: Words that flip the plain reading of a rationale. Used to catch rows where the
#: tagger's sentiment contradicts its own text — e.g. FMX came through tagged
#: Bear on "significant future growth potential".
_BULL_WORDS = ("growth", "undervalued", "upside", "beat", "positive", "strong",
               "promising", "accelerat", "approval", "potential", "outperform")
_BEAR_WORDS = ("deceleration", "deteriorat", "miss", "weak", "soft", "decline",
               "downgrade", "slowdown", "risk", "falling", "overvalued", "cut")


def _direction_agrees(sentiment: float, rationale: str) -> bool:
    """True when the rationale's own wording matches the tagged direction.

    The tagger emits a sentiment score with a confidence, and both can be high
    while the sentence says the opposite. A row that fails this check is dropped
    rather than published with a direction we cannot stand behind.
    """
    t = (rationale or "").lower()
    bull = sum(w in t for w in _BULL_WORDS)
    bear = sum(w in t for w in _BEAR_WORDS)
    if bull == bear:
        return True  # no signal either way — defer to the tagger
    return (bull > bear) == (sentiment > 0)


def _mentions_subject(sym: str, rationale: str) -> bool:
    """True unless the rationale is plainly about a DIFFERENT company.

    ``AAPL — "Abbott's settlements are expected to positively impact Apple's
    stock"`` shipped as a bull call on Apple sourced from a note about Abbott.
    We can't resolve every name, but a rationale whose FIRST named company is a
    possessive that doesn't match the ticker is a mis-attribution worth dropping.
    """
    t = (rationale or "").strip()
    if not t:
        return False
    low = t.lower()
    if any(w in low for w in _THEME_WORDS):
        return False
    lead = re.match(r"^([A-Z][A-Za-z&.\-]{2,})(?:'s|’s)\b", t)
    if not lead:
        return True
    name = lead.group(1).upper()
    return name.startswith(sym[:3]) or sym.startswith(name[:3])


def _tracker_block(session: Session) -> list[dict[str, Any]]:
    """High-conviction ideas surfaced from the trade-idea letters (data-driven).

    Structured trade parsing (exact strikes/structures) is a follow-up that needs
    letter-body extraction; for now this surfaces the strongest source-tagged
    names, minus the rows whose direction or subject the text contradicts.
    """
    wl = get_research_watchlist(session, active_only=True, limit=200)
    tradeable = _tradeable(session)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    dropped = 0
    for r in wl.get("rows") or []:
        sym = (r.get("symbol") or "").strip().upper()
        sent, conf = r.get("sentiment"), r.get("confidence")
        if not _is_clean_ticker(sym) or sym in seen or sent is None or conf is None:
            continue
        if abs(sent) < 0.8 or conf < 0.8:
            continue
        if tradeable and sym not in tradeable:
            dropped += 1
            continue
        rationale = (r.get("rationale") or "").strip()
        if not _direction_agrees(sent, rationale) or not _mentions_subject(sym, rationale):
            dropped += 1
            continue
        seen.add(sym)
        mention = _is_mention_only(rationale)
        out.append(
            {"src": "letters", "ticker": sym,
             # A bare mention carries no direction we can stand behind, so the
             # direction badge is withheld rather than guessed.
             "dir": "—" if mention else ("Bull" if sent > 0 else "Bear"),
             "note": snippet(rationale, 90),
             "status": "named only" if mention else "thesis",
             "mention_only": mention}
        )
        if len(out) >= 12:
            break
    if dropped:
        log.info("daily_brief.tracker_rows_dropped", n=dropped)
    out.sort(key=lambda r: r["mention_only"])  # theses first, mentions after
    return out[:8]


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


#: Claims we can mechanically verify against our own tape, and the phrasings a
#: letter uses to make them. A row is emitted ONLY when a current letter actually
#: contains the claim — the old version hard-coded both rows and stamped them
#: "Doc / L&S" whether or not either letter had said anything of the kind, which
#: made a template look like verification.
_CLAIM_PROBES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Dealers short-gamma / move-amplifying",
     ("short gamma", "short-gamma", "negative gamma", "move-amplif", "amplify moves")),
    ("Tail-hedge bid into events",
     ("tail hedge", "tail-hedge", "crash bid", "vvix", "put bid", "hedging demand")),
    ("Compressed / pinned range",
     ("pinned", "pin ", "compressed range", "chop zone", "range-bound", "low energy")),
)


def _crosschecks(
    indices: list[dict[str, Any]],
    vix: dict[str, Any],
    letters: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Letter claims vs our tape — only for claims a CURRENT letter actually made.

    Each row names the sources whose fresh text contains the claim. When no
    current letter makes a checkable claim the section is empty, which is the
    honest output: nothing was claimed, so nothing was checked.
    """
    corpus = [
        (l.get("src") or "?", (l.get("text") or "").lower())
        for l in (letters or [])
        if l.get("fresh") and l.get("text")
    ]
    below = [ix["symbol"] for ix in indices if (ix.get("spot_vs_flip_pct") or 0) < 0]
    vvix = vix.get("vvix")
    pinned = not below and all(
        abs(ix.get("spot_vs_flip_pct") or 0) < 1.0 for ix in indices
    )

    def _ours(claim: str) -> tuple[str, bool]:
        if claim.startswith("Dealers short-gamma"):
            return (f"{', '.join(below)} below flip" if below else "all indices above flip",
                    bool(below))
        if claim.startswith("Tail-hedge"):
            return (f"VVIX {vvix:.0f}, VIX call-wall {vix.get('call_wall') or '—'}"
                    if vvix is not None else "VVIX unavailable",
                    bool(vvix is not None and vvix > 95))
        return ("all indices within 1% of flip" if pinned else "spot dispersed from flip",
                pinned)

    out: list[dict[str, Any]] = []
    for claim, probes in _CLAIM_PROBES:
        srcs = sorted({src for src, txt in corpus if any(pr in txt for pr in probes)})
        if not srcs:
            continue
        ours, agrees = _ours(claim)
        out.append({
            "claim": claim,
            "source": " / ".join(srcs),
            "our": ours,
            "verdict": "✅ confirmed" if agrees else "◻︎ not in our data",
            "cls": "ok" if agrees else "dim",
        })
    return out


_LETTER_SOURCES = (
    ("__DOC__", "Doc McGraw"),
    ("__LONGSHORT__", "The Long & Short"),
    ("__JAGUAR__", "Jaguar Analytics"),
    ("__SITS__", "Special Situations"),
)


def _letters_block(session: Session, today: date | None = None) -> list[dict[str, Any]]:
    """Newest stored body per source, cleaned of email chrome and age-stamped.

    Bodies used to be sliced raw (``note_md[:320]``), which is how Substack's
    "View this post on the web at ..." preamble and Mailchimp's inline CSS sheet
    ended up rendered as market commentary.
    """
    today = today or date.today()
    out: list[dict[str, Any]] = []
    for key, label in _LETTER_SOURCES:
        note = get_research_note(session, key)
        if not (note.get("found") and note.get("note_md")):
            continue
        body = clean_body(note["note_md"])
        if len(body) < 40:
            continue
        age = _letter_age(note, today)
        out.append({
            "src": label,
            "text": snippet(body, 320),
            "as_of": age["as_of"],
            "age": age["age"],
            "age_label": age["label"],
            "fresh": age["fresh"],
        })
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


def _em_levels_block(
    session: Session, spot_override: float | None = None
) -> dict[str, Any] | None:
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
    # The RAILS are anchored on SPY closes, but "where is price now" must be the
    # SAME number the rest of the brief prints. This block used to fall back to
    # the last SPY close x10 while the board showed the live greeks spot, so one
    # page carried 7,702 (SPY x10) and 7,719 (greeks) for SPX simultaneously.
    cur_spx = float(spot_override) if spot_override else cur_spy * _SPX_FROM_SPY
    spot_src = "index board" if spot_override else "SPY x10"
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
        elif pos >= 85:
            status = "at the upper rail"
        elif pos >= 65:
            status = "upper third"
        elif pos <= 15:
            status = "at the lower rail"
        elif pos <= 35:
            status = "lower third"
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
        "current_src": spot_src,
        "anchor_src": "SPY×10",
        "as_of": cur_date.isoformat(),
        "rows": out,
    }


_MAG7 = ("AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA")


def _mag7_block(session: Session) -> list[dict[str, Any]]:
    """Mag7 gamma/vol snapshot — the mega-caps that drive the index."""
    out: list[dict[str, Any]] = []
    for sym in _MAG7:
        rows = _trading_sessions(get_gamma_history(session, sym, days=8).get("rows") or [])
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
    """Top single-name option-flow names by premium traded (our own tape).

    ``total_notional`` is PREMIUM traded; ``net_dollar_delta`` is DELTA-notional.
    They are different units and routinely differ by 5x, so printing them in
    adjacent unlabelled "$" columns produced rows like "EWY $48.7M / $136.4M" —
    a net that appears to exceed the total. Both are carried through with their
    unit named, and ``tilt_conflict`` flags a row whose label disagrees with its
    own buy-tilt so the render can mark it instead of asserting it.
    """
    sc = get_flow_scorecard(session, lookback_days=5, min_notional=1_000_000.0, limit=40)
    rows = sorted(
        sc.get("rows") or [], key=lambda r: (r.get("total_notional") or 0.0), reverse=True
    )[:5]
    out: list[dict[str, Any]] = []
    for r in rows:
        tilt, label = r.get("buy_tilt"), (r.get("label") or "")
        out.append(
            {
                "root": r.get("root"),
                "premium": r.get("total_notional"),
                "net_delta": r.get("net_dollar_delta"),
                "label": label,
                "score": r.get("accum_score"),
                "days_observed": r.get("days_observed"),
                "buy_tilt": tilt,
                "tilt_conflict": bool(
                    tilt is not None
                    and ((tilt < 0 and label == "accumulation")
                         or (tilt > 0 and label == "distribution"))
                ),
            }
        )
    return out


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
        facts.append(
            f"SPY last {spy.get('spot'):,.2f} — {side} its {spy['flip']:,.2f} gamma flip"
        )
    if em_levels:
        wk = next((r for r in (em_levels.get("rows") or []) if r.get("tenor") == "Weekly"), None)
        if wk and wk.get("lower") and wk.get("upper"):
            facts.append(f"{wk.get('status')} of the weekly rail ({wk['lower']:.0f}–{wk['upper']:.0f})")
    if vix.get("vix") is not None:
        facts.append(f"VIX {vix.get('vix')} / VVIX {vix.get('vvix')}")
    return {
        "recap": ("; ".join(facts) + ".") if facts else "",
        "outlook": snippet(doc.get("expectation") or "", 400),
        "outlook_src": doc.get("expectation_src"),
        "outlook_fresh": bool((doc.get("letter_age") or {}).get("fresh")),
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
    today = date.today()
    # The brief is written FOR a trading session. Dating it date.today() meant the
    # 2026-09-07 edition called itself a "pre-open daily brief" on Labor Day.
    session_date = _prev_session(today)
    indices = [b for s in _INDEX_ROOTS if (b := _index_block(session, s)) is not None]
    vix = _vix_block(session)
    doc_index = next((ix for ix in indices if ix["symbol"] == _DOC_ROOT), None)
    doc = _doc_block(session, doc_index, vix.get("vix"), today)
    em_levels = _em_levels_block(session, (doc_index or {}).get("spot"))
    recap = _recap_block(indices, em_levels, vix, doc)
    mag7 = _mag7_block(session)
    flows = _flows_block(session)
    letters = _letters_block(session, today)
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
        "as_of": today.isoformat(),
        "session_date": session_date.isoformat(),
        "is_market_open_today": not is_market_holiday(today),
        "subtitle": (
            "pre-open daily brief · synthesis read, index gamma, Doc levels, letters"
            if not is_market_holiday(today)
            else f"market closed today · data through the {session_date:%b %d} session"
        ),
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
        "letters": letters,
        "fresh_tags": None,
        "tracker": _tracker_block(session),
        "learned": learned,
        "learned_total": learned_total,
        "crosschecks": _crosschecks(indices, vix, letters),
        "board_note": (
            "Flip trend and net-GEX sparklines run left→right over the last "
            f"{_SPARK_POINTS} TRADING sessions — one EOD point per open day "
            "(weekends and market holidays excluded). Spot-vs-flip colored "
            "green (long γ) / red (short γ)."
        ),
        "vol_note": "VRP positive = implied rich vs realized; elevated VVIX = paying up for the vol path.",
        "provenance": "index γ + VIX + walls/straddle via trading-intel NAS · letters ingest (research watchlist).",
    }
    return ctx


def build_brief_html(session: Session, settings: Settings | None = None) -> tuple[str, dict[str, Any]]:
    """Return (html, context) for the daily brief."""
    ctx = build_brief_context(session, settings)
    return render_html(ctx), ctx
