"""Assemble the Jaguar daily brief.

Reads the three core emails (:mod:`jaguar.source`), grounds tickers/flow with the
deterministic parser, condenses his reasoning with local Ollama (rule 7), cross-checks
each named print against our own tape, computes S&P breadth, proposes one defined-risk
structure per name, and hands the assembled dict to :mod:`jaguar.render`. Every external
call is wrapped so a missing service (Gmail, Ollama, a DB tool, the breadth feed)
degrades that one piece instead of failing the brief. His calls are relayed
descriptively; the ⚡ structures are illustrative analysis, never a signal (rule 4).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date
from typing import Any, TypeVar

import structlog
from sqlalchemy.orm import Session

from trading_intel.config import Settings, get_settings
from trading_intel.jaguar import extract, parse, pricing, render, source
from trading_intel.jaguar.structure import call_spread, short_strike_for_move
from trading_intel.market import breadth as breadth_mod
from trading_intel.mcp.extra_tools import (
    get_flow_scorecard,
    get_oi_changes,
    get_straddle,
    get_walls,
)

log = structlog.get_logger(__name__)
_T = TypeVar("_T")

_STK = re.compile(
    r"([A-Z][a-z]+)\s+(?:\(\d+\)\s+)?(?:Weekly\s+)?(\d{2,4})(?:[-/]\d+)*(?:-strike)?\s+call", re.I
)
_MACRO_KW = (
    "nasdaq",
    "semis",
    "asml",
    "duv",
    "fed",
    "fomc",
    "tariff",
    "seasonal",
    "bearish",
    "bear market",
    "correction",
)
_TAG_ER = re.compile(r"\b(?:Tue|Tuesday|Wed|Wednesday|Thu|Thursday|Mon|Monday|Fri|Friday)\b", re.I)


def _safe(fn: Callable[[], _T]) -> _T | None:
    try:
        return fn()
    except Exception:
        return None


def _rank(callouts: list[parse.Callout]) -> list[parse.Callout]:
    """Earnings + real flow + a premium float to the top; order otherwise preserved."""

    def score(c: parse.Callout) -> int:
        return (2 if c.earnings else 0) + (2 if c.contracts else 0) + (1 if c.premium else 0)

    return sorted(callouts, key=score, reverse=True)


def _flow_line(c: parse.Callout) -> str:
    if c.contracts:
        line = c.contracts[0]
        return line if len(line) <= 150 else line[:147] + "…"
    return c.premium or ""


def _our_tape(session: Session, ticker: str) -> str:
    """Descriptive cross-check of Fahad's print against our banked tape."""
    bits: list[str] = []
    oi = _safe(lambda: get_oi_changes(session, ticker))
    if oi and oi.get("found") and oi.get("net_call_oi_change") is not None:
        bits.append(f"our net call OI Δ {oi['net_call_oi_change']:+,.0f}")
    walls = _safe(lambda: get_walls(session, ticker))
    if walls and walls.get("found") and walls.get("call_wall"):
        bits.append(f"call wall {walls['call_wall']:g}")
    fs = _safe(
        lambda: get_flow_scorecard(session, lookback_days=20, min_notional=500_000.0, limit=200)
    )
    row = next((r for r in (fs or {}).get("rows", []) if r.get("root") == ticker), None)
    if row:
        bits.append(f"flow {row.get('label')} (Δ$ {row.get('net_dollar_delta', 0):+,.0f})")
    st = _safe(lambda: get_straddle(session, ticker))
    if st and st.get("found"):
        bits.append("expected-move straddle in our chain")
    if not bits:
        return f"{ticker} isn't in our options tape — relayed as Fahad's observation only."
    return " · ".join(bits)


def _structure_for(
    c: parse.Callout,
    *,
    cvforge: object | None = None,
    chain_cache: dict[str, Any] | None = None,
    ref_date: date | None = None,
) -> dict[str, Any] | None:
    """One defined-risk call spread off the strike the smart money used.

    Legs price from the CVForge chain's stored IV when a chain is available, filling
    MAX-RISK / TARGET; otherwise the strikes still render as "live-priced". The chain
    is pulled once per ticker via ``chain_cache``.
    """
    m = _STK.search(" ".join(c.contracts) or "") or _STK.search(c.text)
    if not m:
        return None
    month = m.group(1)[:3].title()
    long_strike = float(m.group(2))
    short_strike = short_strike_for_move(long_strike)

    long_px = short_px = None
    if cvforge is not None:
        if chain_cache is not None and c.ticker in chain_cache:
            chain = chain_cache[c.ticker]
        else:
            chain = _safe(lambda: cvforge.chain(c.ticker))
            if chain_cache is not None:
                chain_cache[c.ticker] = chain
        if chain is not None:
            marks = pricing.price_call_spread(
                chain, month, long_strike, short_strike, ref_date=ref_date
            )
            long_px, short_px = marks.get("long_price"), marks.get("short_price")

    note = "Mirrors the strike/window the smart-money buyer chose; downside capped at the debit, sized for a strong multiple to the short strike."
    st = call_spread(
        c.ticker,
        month,
        long_strike,
        short_strike,
        long_price=long_px,
        short_price=short_px,
        rationale=note,
    )
    return {
        "label": st.label,
        "max_risk": st.max_risk,
        "target_pct": st.target_pct,
        "breakeven": st.breakeven,
        "note": note,
    }


def _thinking(body: str, *, llm: Any, model: str | None) -> dict[str, Any]:
    blocks = parse.blocks(body)
    macro = [b for b in blocks if any(k in b.lower() for k in _MACRO_KW)]
    big = (
        extract.condense("\n".join(macro)[:2400], llm=llm, model=model, sentences=3)
        if macro
        else ""
    )
    tactical_src = next(
        (b for b in blocks if "google" in b.lower() or "defense" in b.lower()),
        "",
    )
    tactical = (
        extract.condense(tactical_src, llm=llm, model=model, sentences=2) if tactical_src else ""
    )
    if not big and not tactical:
        hl = parse.first_read_highlights(body)
        big = " · ".join(hl[:3])
    return {"big_picture": big, "tactical": tactical, "moat": [], "extra": ""}


def _breadth(cvforge: object | None) -> dict[str, Any]:
    closes: dict[str, list[float]] = {}
    if cvforge is not None:
        syms = _safe(lambda: breadth_mod.sp500_symbols(cvforge)) or []
        if syms:
            closes = _safe(lambda: breadth_mod.fetch_closes(cvforge, syms)) or {}
    foot = (
        "S&P 500-wide: constituents via FMP (existing vendor / rule 1); % above 50/200-day MA, "
        "A/D and highs–lows computed on the NAS. Descriptive context only (rule 4)."
    )
    if not closes:
        return {
            "index": [],
            "rows": [("S&P breadth", "computing", "")],
            "read": "Constituent feed warming up — breadth fills on the next run.",
            "foot": foot,
        }
    b = breadth_mod.compute_breadth(closes)
    trend = "·".join(str(x) for x in b.trend_50 if x is not None)
    falling = (
        b.trend_50
        and b.trend_50[0] is not None
        and b.trend_50[-1] is not None
        and b.trend_50[-1] < b.trend_50[0]
    )
    read = (
        f"% above the 50-day has moved {b.trend_50[0]}%→{b.trend_50[-1]}% over the window and "
        f"advancers/decliners are {b.advancers}/{b.decliners} — "
        + ("participation is thinning under the index." if falling else "participation is holding.")
    )
    return {
        "index": [],
        "rows": [
            ("% S&P above 50-day MA", f"{b.pct_above_50}%", trend),
            ("% S&P above 200-day MA", f"{b.pct_above_200}%", ""),
            ("Advancers / decliners", f"{b.advancers} / {b.decliners}", ""),
            ("New 52-wk highs / lows", f"{b.new_highs} / {b.new_lows}", ""),
        ],
        "read": read,
        "foot": foot,
    }


def _changed(live_body: str, ta: dict[str, Any]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if ta.get("subject"):
        out.append(("New Trade Alert", ta["subject"].split(" - ", 1)[-1]))
    conv = parse.find_block(live_body, "Conversations") or parse.find_block(live_body, "Summary:")
    if conv:
        out.append(("Conversations", extract.first_sentences(conv, 2)))
    wr = parse.find_block(live_body, "Weekend Research")
    if wr:
        out.append(("Weekend Research", extract.first_sentences(wr, 2)))
    return out


def _macro(fr_body: str, *, llm: Any, model: str | None) -> tuple[str, str]:
    hl = parse.first_read_highlights(fr_body)
    facts = " · ".join(hl[:4]) if hl else extract.first_sentences(fr_body, 2)
    read = extract.condense(fr_body[:2400], llm=llm, model=model, sentences=3) if fr_body else ""
    return facts, read


def build_jaguar_brief(
    session: Session,
    settings: Settings | None = None,
    *,
    llm: Any = None,
    cvforge: object | None = None,
    core: dict[str, dict] | None = None,
    max_trades: int = 6,
) -> tuple[str, dict[str, Any]]:
    """Assemble and render the daily brief. Returns ``(html, brief_dict)``."""
    settings = settings or get_settings()
    if llm is None:
        from trading_intel.synthesis.llm import OllamaProvider

        llm = _safe(lambda: OllamaProvider(settings))
    model = getattr(settings, "LLM_DAILY_MODEL", None)
    if core is None:
        core = _safe(lambda: source.fetch_core(settings)) or {}

    live = core.get("jaguarlive", {}) or {}
    fr = core.get("first_read", {}) or {}
    ta = core.get("trade_alert", {}) or {}
    live_body = live.get("body", "") or ""

    callouts = _rank(parse.parse_callouts(live_body))
    chain_cache: dict[str, Any] = {}
    ref_day = date.today()
    trades: list[dict[str, Any]] = []
    for c in callouts[:max_trades]:
        him = (
            extract.condense(c.text, llm=llm, ticker=c.ticker, model=model)
            if llm
            else extract.first_sentences(c.text, 3)
        )
        tag = "earnings" if c.earnings else ""
        trades.append(
            {
                "ticker": c.ticker,
                "name": c.name,
                "tag": tag,
                "tag_kind": "er" if c.earnings else "",
                "flow": _flow_line(c),
                "him": him,
                "ours": _our_tape(session, c.ticker),
                "structure": _structure_for(
                    c, cvforge=cvforge, chain_cache=chain_cache, ref_date=ref_day
                ),
                "links": [("his note", u) for u in c.links[:2]],
            }
        )
    smaller = " · ".join(
        f"{c.ticker} {(_flow_line(c) or '')[:60]}".strip()
        for c in callouts[max_trades : max_trades + 4]
    )

    macro_facts, macro_read = _macro(fr.get("body", "") or "", llm=llm, model=model)
    brief: dict[str, Any] = {
        "as_of": date.today().strftime("%a %b %d %Y"),
        "banner": "Built from your Jaguar emails — JaguarLive, First Read, Trade Alert. His read → ⊕ our tape → ⚡ combined structure.",
        "sub": "His trades & the flow he's following → our tape → one defined-risk structure. Then his thinking, S&P breadth, what changed, macro.",
        "trades": trades,
        "smaller": smaller,
        "thinking": _thinking(live_body, llm=llm, model=model),
        "breadth": _breadth(cvforge),
        "changed": _changed(live_body, ta),
        "macro_facts": macro_facts,
        "macro_read": macro_read,
        "foot": "NAS-native: Gmail (Trash-fix) → Ollama condense (rule 7) → our tape + S&P breadth → Telegram. Descriptive relay + our analysis, never an automated signal (rule 4).",
    }
    log.info("jaguar.brief.built", trades=len(trades), kinds=sorted(core))
    return render.build_html(brief), brief
