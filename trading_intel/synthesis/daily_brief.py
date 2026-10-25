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
    # the last CLOSE strictly before today — the anchor for the SPY-implied pre-open
    # spot (the newest row can be today's 09:45 intraday snapshot when the brief is
    # re-run during the session, and that is not a close)
    today = date.today()
    prev = next((r for r in reversed(rows) if (r.get("session") or _as_date(r.get("date")) or today) < today), None)
    return {
        "symbol": symbol,
        "spot": spot,
        "prev_close": prev.get("spot") if prev else None,
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
    spot = (doc_index or {}).get("spot")
    # Doc's levels are the WEEK's hedging ("the heaviest hedging sits at the
    # week's low"), so the walls he is compared against come from the 1–7 DTE
    # book. The 60-day book is kept only for the monthly magnet (the 8,000 pile).
    walls = get_walls(session, _DOC_ROOT, dte_max=7, spot_override=spot)
    monthly = get_walls(session, _DOC_ROOT, dte_max=60, spot_override=spot)
    strad = get_straddle(session, _DOC_ROOT, dte_max=7)
    flip = (doc_index or {}).get("flip")
    spot = spot or walls.get("spot")
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
        "walls_dte": walls.get("dte_max"),
        "magnet": (monthly.get("magnet") or {}).get("strike"),
        "monthly_call_wall": monthly.get("call_wall"),
        "monthly_put_wall": monthly.get("put_wall"),
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


def _expiry_clock_block(today: date) -> dict[str, Any] | None:
    """Where we are in the OpEx / VIXpiration / quarter cycle + the read (trading_intel.expiry_clock)."""
    try:
        from trading_intel.expiry_clock import phase

        return phase(today)
    except Exception:  # noqa: BLE001 — additive; the brief renders without it
        return None

#: Newsletter level names that are SPX PRICES. A value outside ±15 % of spot
#: under one of these names is an extractor mis-tag, not a level: the Ollama
#: pass filed "gamma is expected to flip" as gamma_flip=10 and "VIX was at 19"
#: as put_wall=19 (Doc, 2026-09-21), and Norseman's Huginn-fund 4,000 / 3,000
#: as SPX resistance / support — all of which then printed in the overlay.
_PRICE_LEVELS = frozenset({
    "gamma_flip", "flip", "zero_gamma", "call_wall", "put_wall", "key_support",
    "key_resistance", "support", "resistance", "target", "bull_bear_line",
})
_PRICE_BAND = 0.15


def _sanitize_levels(
    newsletter: dict[str, Any] | None, spot: float | None, dropped: list[dict[str, Any]] | None = None
) -> dict[str, Any] | None:
    """Drop price-type newsletter levels that cannot be SPX prices near spot.

    ``dropped`` (when given) collects what was filtered, so the brief can show
    the extractor's mis-tags instead of hiding them.
    """
    if not newsletter or not spot:
        return newsletter
    lo, hi = spot * (1 - _PRICE_BAND), spot * (1 + _PRICE_BAND)
    out = dict(newsletter)
    srcs = {}
    for name, src in (newsletter.get("sources") or {}).items():
        src = dict(src or {})
        kept = []
        for lv in src.get("levels") or []:
            nm = (lv.get("name") or "").lower()
            v = lv.get("value")
            try:
                fv = float(v) if v is not None else None
            except (TypeError, ValueError):
                fv = None
            if nm in _PRICE_LEVELS and (fv is None or not (lo <= fv <= hi)):
                if dropped is not None:
                    dropped.append({"source": name, "name": nm, "value": v, "note": lv.get("note")})
                continue
            kept.append(lv)
        src["levels"] = kept
        srcs[name] = src
    out["sources"] = srcs
    return out


def _spy_implied_spot(spx_close: float | None, today: date) -> dict[str, Any] | None:
    """SPX pre-open = SPX close × (SPY extended-hours last ÷ SPY previous close).

    SPY trades from 04:00; SPX does not print until 09:30. This is the cockpit's
    pre-open rule, moved into the brief. FMP ``aftermarket-trade`` is the
    extended-hours tape (price + timestamp); ``quote`` carries previousClose.
    Basis drift between the ETF and cash is a few points. None when FMP is
    unavailable or the last trade is not from today.
    """
    if not spx_close:
        return None
    try:
        from datetime import datetime

        from trading_intel.clients.fmp import FmpClient
        from trading_intel.timeutils import EASTERN

        c = FmpClient(get_settings())
        trade = c.fmp("aftermarket-trade", {"symbol": "SPY"}) or []
        quote = c.fmp("quote", {"symbol": "SPY"}) or []
        t = trade[0] if isinstance(trade, list) and trade else {}
        q = quote[0] if isinstance(quote, list) and quote else {}
        px, prev, ts = t.get("price"), q.get("previousClose"), t.get("timestamp")
        if not (px and prev and ts):
            return None
        when = datetime.fromtimestamp(float(ts) / (1000.0 if float(ts) > 1e11 else 1.0), tz=EASTERN)
        if when.date() != today:
            return None
        return {"spot": round(float(spx_close) * float(px) / float(prev), 2),
                "spy": float(px), "spy_prev": float(prev), "at": when.strftime("%H:%M")}
    except Exception:  # noqa: BLE001 — quote feed down: the chain fallback still works
        log.warning("daily_brief.spy_spot_failed", exc_info=True)
        return None


def _pre_open_spot(session: Session, today: date, spx_close: float | None = None) -> dict[str, Any] | None:
    """Pre-open SPX: SPY-implied (primary) with the 08:35 chain's Δ=0.50 strike as the check.

    Both are kept so the gap between them can be audited; a gap over 0.4 % is
    logged (a stale chain, a wide pre-open quote, or a bad SPY print).
    """
    chain = None
    try:
        from trading_intel.synthesis.zero_dte import chain_spot

        cs = chain_spot(session, _DOC_ROOT)
        if cs and cs["ts"].date() == today:
            chain = cs["spot"]
    except Exception:  # noqa: BLE001
        log.warning("daily_brief.chain_spot_failed", exc_info=True)
    spy = _spy_implied_spot(spx_close, today)
    if spy:
        out = {"spot": spy["spot"], "label": f"SPY {spy['spy']:.2f} at {spy['at']} vs close {spy['spy_prev']:.2f}",
               "src": "spy", "chain_spot": chain, "spy": spy}
    elif chain is not None:
        out = {"spot": chain, "label": "08:35 index chain (Δ=0.50 strike)", "src": "chain", "chain_spot": chain}
    else:
        return None
    if chain is not None and out["spot"]:
        gap = abs(out["spot"] - chain) / out["spot"] * 100.0
        out["gap_pct"] = round(gap, 2)
        if gap > 0.4:
            log.warning("daily_brief.pre_open_spot_gap", spy_implied=out["spot"], chain=chain, gap_pct=round(gap, 2))
    out["ts"] = today.isoformat()
    return out


def _apply_pre_open_spot(indices: list[dict[str, Any]], pre: dict[str, Any] | None, today: date) -> None:
    """Re-mark the SPX board row to the pre-open spot when the board is yesterday's close."""
    if not pre:
        return
    for ix in indices:
        if ix.get("symbol") != _DOC_ROOT or not ix.get("flip"):
            continue
        if (_as_date(ix.get("asof")) or today) >= today:
            return
        from trading_intel.dashboard.watchlist_metrics import gamma_regime

        spot, flip = pre["spot"], ix["flip"]
        ix.update({
            "close_spot": ix.get("spot"),
            "spot": spot,
            "spot_vs_flip_pct": (spot - flip) / flip * 100.0,
            "regime": gamma_regime(spot, flip),
            "spot_src": pre["label"],
        })


def _book_read(ctx: dict[str, Any]) -> list[dict[str, str]]:
    """Today's numbers read in the dealer-flow frame (Alma / OptionsDepth, 2026-10-22 corpus).

    Rules only — every line names the number it is built on, and the condition
    that would kill it. Descriptor, not a signal (rule 4).
    """
    out: list[dict[str, str]] = []
    spx = next((ix for ix in (ctx.get("indices") or []) if ix.get("symbol") == "SPX"), None) or {}
    doc = ctx.get("doc") or {}
    z = ctx.get("zero_dte") or {}
    vix = ctx.get("vix") or {}
    sk = ctx.get("vol_skew") or {}
    spot, flip = spx.get("spot"), spx.get("flip")
    f0 = lambda v: f"{v:,.0f}" if isinstance(v, (int, float)) else "—"  # noqa: E731

    # 1. regime — the line, and what kills the read
    if spot and flip:
        d = spot - flip
        if d < 0:
            out.append({"t": "Short gamma", "s": (
                f"SPX {f0(spot)} is {abs(d):,.0f} pts below the {f0(flip)} flip: dealers chase — dips extend, "
                "rallies are sold only into the walls. Fading extremes is wrong here; the read flips the minute "
                f"{f0(flip)} is reclaimed (a line, not a target).")})
        else:
            out.append({"t": "Long gamma", "s": (
                f"SPX {f0(spot)} is {d:,.0f} pts above the {f0(flip)} flip: dealers damp — buy dips / sell rips "
                "between the walls, so extremes mean-revert. The read dies on a close below "
                f"{f0(flip)}; structure holds only while the book keeps its shape.")})

    # 2. the 0DTE book — intraday line, not the EOD one
    if z.get("flip") or z.get("range"):
        rng = z.get("range") or (None, None)
        out.append({"t": "0DTE book", "s": (
            f"Today's expiry alone is {z.get('net_sign') or 'flat'} with its own flip at {f0(z.get('flip'))} "
            f"(walls {f0(z.get('put_wall'))} / {f0(z.get('call_wall'))}); 1σ for the session {f0(rng[0])}–{f0(rng[1])}. "
            "Intraday the live 0DTE book sets the chase/damp line, not the EOD one — the cockpit Map tab carries it "
            "forward through the clock; charm from this book only matters in the last hours and only if the day stays quiet.")})

    # 3. walls — hold/break by whether vol there is bid or offered (skew percentile as the proxy)
    cw, pw, mag = doc.get("call_wall"), doc.get("put_wall"), doc.get("magnet")
    if cw or pw:
        rr = sk.get("rr_pctile")
        if isinstance(rr, (int, float)) and rr <= 1.0:
            rr = rr * 100.0  # stored as a fraction of the 252d window
        if isinstance(rr, (int, float)) and rr >= 70:
            vol_s = (f"25Δ skew sits at the {rr:.0f}th pctile — puts are BID, so the {f0(pw)} put wall is under pressure: "
                     "a wall whose vol is bid breaks; dealers are short skew into it and sell spot-down/vol-up.")
        elif isinstance(rr, (int, float)) and rr <= 30:
            vol_s = (f"25Δ skew sits at the {rr:.0f}th pctile — downside vol is OFFERED, so the {f0(pw)} put wall should hold: "
                     "dealers sell vol into the dip and the vanna loop (vol down → dealer buying) is supportive.")
        else:
            vol_s = (f"25Δ skew is mid-range ({rr:.0f}th pctile) — the walls hold or break on whether fixed-strike vol at "
                     f"{f0(pw)} / {f0(cw)} is offered or bid today; watch the fixed-strike IV, not spot."
                     if isinstance(rr, (int, float)) else
                     f"Walls hold when fixed-strike vol at {f0(pw)} / {f0(cw)} is offered and break when it is bid.")
        mag_s = (f" The {f0(mag)} pile in the 60-day book is overwriter/collar paper — a cap that pins into the monthlies, "
                 "not a target for today." if mag and mag not in (cw, pw) else "")
        out.append({"t": "Walls (1–7 DTE book)", "s": f"Put wall {f0(pw)} · call wall {f0(cw)}. {vol_s}{mag_s}"})

    # 4. vol state — which greek is driving
    v, vv, term, vrp = vix.get("vix"), vix.get("vvix"), vix.get("term"), vix.get("vrp")
    parts = []
    if isinstance(term, (int, float)):
        parts.append(f"9d−3m {term:+.1f} = {'contango, calm weather' if term < 0 else 'BACKWARDATION — stress, event vol in the front'}")
    if isinstance(vrp, (int, float)):
        if vrp > 3:
            parts.append(f"VRP +{vrp:.1f} pts: implied is rich to realized, so decay (charm/theta) is the dealers' flow on a "
                         "quiet day — late-session charm dominates only while realized stays below implied (Alma: "
                         "R = 0.63·(IV/RV)·√(Δt/τ)); a move beyond the straddle hands it back to gamma")
        elif vrp < 0:
            parts.append(f"VRP {vrp:.1f}: realized is running ABOVE implied — gamma, not charm, is the flow; short-vol "
                         "structures are losing and the dealers' hedging is the trend's fuel")
        else:
            parts.append(f"VRP {vrp:+.1f}: implied ≈ realized, no decay edge either way")
    if isinstance(vv, (int, float)) and vv > 95:
        parts.append(f"VVIX {vv:.0f}: the tape pays up for the vol path — vanna is live, and a vol crush is dealer buying "
                     "(spot-up/vol-down loop) while a vol pop is dealer selling")
    cor = sk.get("cor1m")
    if isinstance(cor, (int, float)) and cor < 15:
        parts.append(f"1M implied correlation {cor:.1f}: dispersion — index vol is suppressed by names moving against "
                     "each other; the risk is correlation snapping to 1 (the beta reset), which lifts index vol without any new news")
    if parts:
        out.append({"t": f"Vol state (VIX {v:.1f})" if isinstance(v, (int, float)) else "Vol state", "s": "; ".join(parts) + "."})

    # 5. the calendar
    for r in (ctx.get("expiry_clock") or {}).get("reads") or []:
        out.append({"t": "Expiry calendar", "s": r})

    # 6. Doc ∩ our book — only his levels that sit on our walls / flip
    lv = (((ctx.get("newsletter") or {}).get("sources") or {}).get("DOC") or {})
    hits = []
    if lv.get("as_of") and lv.get("as_of") == (doc.get("letter_age") or {}).get("as_of"):
        ours = {"put wall": pw, "call wall": cw, "flip": flip}
        for l in lv.get("levels") or []:
            val = l.get("value")
            if not isinstance(val, (int, float)):
                continue
            for nm, ov in ours.items():
                if ov and abs(val - ov) / ov <= 0.002:
                    hits.append(f"Doc's {l.get('name', '').replace('_', ' ')} {f0(val)} = our {nm}")
    if hits:
        out.append({"t": "Doc ∩ book", "s": "; ".join(dict.fromkeys(hits)) + "."})
    out.append({"t": "Caveat", "s": "Positions are naive OI (customers long puts / short calls), not cleared positioning — "
                "a put-heavy book reads short gamma without that being true. Rules, not a forecast."})
    return out


def build_brief_context(session: Session, settings: Settings | None = None) -> dict[str, Any]:
    """Assemble the full daily-brief context dict from banked data."""
    settings = settings or get_settings()
    today = date.today()
    # The brief is written FOR a trading session. Dating it date.today() meant the
    # 2026-09-07 edition called itself a "pre-open daily brief" on Labor Day.
    # The brief is PRE-OPEN: the board it describes closed the session BEFORE
    # today. ``_prev_session(today)`` returned today itself on any open day, so
    # the 08:45 edition said "data through the 2026-10-22 close" at 8 am.
    session_date = _prev_session(today - timedelta(days=1))
    indices = [b for s in _INDEX_ROOTS if (b := _index_block(session, s)) is not None]
    spx_close = next((ix.get("prev_close") for ix in indices if ix.get("symbol") == _DOC_ROOT), None)
    pre_open = _pre_open_spot(session, today, spx_close) if not is_market_holiday(today) else None
    _apply_pre_open_spot(indices, pre_open, today)
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
        from trading_intel.synthesis.zero_dte import build_zero_dte

        zero_dte = build_zero_dte(session, "SPX", today=today) if not is_market_holiday(today) else None
    except Exception:  # noqa: BLE001 — optional panel
        zero_dte = None
    filtered_levels: list[dict[str, Any]] = []
    try:
        newsletter = _sanitize_levels(build_newsletter_signals(session), (doc_index or {}).get("spot"), filtered_levels)
    except Exception:  # noqa: BLE001
        newsletter = None
    if market_read and pre_open and (doc_index or {}).get("spot"):
        # The fused read is built from the aggregate snapshot (yesterday's close
        # pre-open); re-mark its spot + distance-to-flip to the chain-implied one.
        mech = market_read.setdefault("mechanics", {})
        sp = float(doc_index["spot"])
        mech["spot"] = sp
        if mech.get("gex_flip"):
            mech["dist_to_flip"] = (sp - float(mech["gex_flip"])) / sp
        for lv in market_read.get("levels") or []:
            if (lv.get("name") or "").lower() == "spot":
                lv["value"] = sp
    ctx: dict[str, Any] = {
        "as_of": today.isoformat(),
        "session_date": session_date.isoformat(),
        "is_market_open_today": not is_market_holiday(today),
        "is_monday": today.weekday() == 0,
        "subtitle": (
            "pre-open daily brief · synthesis read, index gamma, Doc levels, letters"
            if not is_market_holiday(today)
            else f"market closed today · data through the {session_date:%b %d} session"
        ),
        "through_line": _through_line(indices, vix),
        "pre_open_spot": pre_open,
        "book_read": [],
        "map_block": None,
        "map_history": [],
        "rolloff": None,
        "filtered_levels": filtered_levels,
        "checks": [],
        "expiry_clock": _expiry_clock_block(today),
        "market_read": market_read,
        "newsletter": newsletter,
        "zero_dte": zero_dte,
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
    # The MAP block reads the flips/walls already in ctx and adds the close
    # odds, delta cones, hedge pressure and the IV±1 fragility check.
    if not is_market_holiday(today):
        try:
            from trading_intel.synthesis.gamma_map import build_map_block
            from trading_intel.synthesis.map_store import (
                graded_count,
                recent_rows,
                record_morning,
            )

            graded = graded_count(session, _DOC_ROOT)
            block = build_map_block(
                session, ctx, symbol=_DOC_ROOT, today=today, graded_sessions=graded
            )
            ctx["map_block"] = block
            if block:
                # Bank the card so tonight's grading job has something to score.
                record_morning(session, block, today=today)
            # The yesterday line + the Monday scorecard read the graded history.
            ctx["map_history"] = recent_rows(session, _DOC_ROOT, days=20, before=today)
            # Roll-off is a Monday / OpEx-day read, not a daily one.
            from trading_intel.market.rolloff import build_rolloff, is_monthly_opex

            if today.weekday() == 0 or is_monthly_opex(today):
                from trading_intel.mcp.extra_tools import get_gex_term

                term = get_gex_term(session, _DOC_ROOT)
                if term.get("found"):
                    ctx["rolloff"] = build_rolloff(term.get("term"), today=today)
        except Exception:  # noqa: BLE001 — additive
            log.warning("daily_brief.map_block_failed", exc_info=True)
    try:
        ctx["book_read"] = _book_read(ctx)
    except Exception:  # noqa: BLE001 — additive
        log.warning("daily_brief.book_read_failed", exc_info=True)
    return ctx


def build_brief_html(session: Session, settings: Settings | None = None) -> tuple[str, dict[str, Any]]:
    """Return (html, context) for the daily brief — with the self-audit run over the context."""
    ctx = build_brief_context(session, settings)
    try:
        from trading_intel.synthesis.brief_checks import run_checks

        ctx["checks"] = run_checks(session, ctx)
    except Exception:  # noqa: BLE001 — the audit must never block the brief
        log.warning("daily_brief.checks_failed", exc_info=True)
    return render_html(ctx), ctx
