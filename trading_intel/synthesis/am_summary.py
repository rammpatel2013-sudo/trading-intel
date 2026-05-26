"""Daily AM report — research-watchlist-aware regime summary.

Composes the morning note from data already collected and stored: the effective
watchlist (static .env symbols UNION active research tickers), the per-ticker
regime metrics, options-flow highlights, the SPX/SPY/QQQ 0DTE intraday read, and
the research-surfaced tickers with their rationale/sentiment. All reads go
through the existing dashboard data layer — this module adds no new queries and
duplicates no compute.

``build_am_context`` is pure (DB in, dataclass out). ``render_am_markdown`` turns
that context into the markdown body: it asks the LLM for a narrative grounded in
the deterministic tables, and falls back to a tables-only report if the LLM is
unavailable (so the daily job always produces a row).

Everything here is a descriptive regime read-through — never a signal, target or
prediction (FlashAlpha rule 4), including for the research-surfaced tickers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd
import structlog
from sqlalchemy.orm import Session

from trading_intel.config import Settings
from trading_intel.dashboard.dynamic_watchlist import load_watchlist_entries
from trading_intel.dashboard.flow_data import load_watchlist_flow
from trading_intel.dashboard.ticker_data import load_intraday_flow_series
from trading_intel.dashboard.watchlist_metrics import load_watchlist_metrics
from trading_intel.synthesis.llm import LLMProvider
from trading_intel.synthesis.prompts import AM_SUMMARY_PROMPT
from trading_intel.timeutils import eastern_now
from trading_intel.watchlist import effective_symbols

log = structlog.get_logger(__name__)

#: Index/ETF symbols that lead the market-regime section (if collected).
_INDEX_SYMBOLS = ("SPX", "SPY", "QQQ")

_RULE4_NOTE = "_Regime descriptors only — not trading signals (FlashAlpha rule 4)._"


# ── Context dataclasses ────────────────────────────────────────────────


@dataclass(frozen=True)
class MarketRead:
    """Market-wide regime read for one index/ETF (SPX/SPY/QQQ)."""

    symbol: str
    spot: float | None
    gex_total: float | None
    gamma_regime: str
    atm_iv: float | None
    gex_dir: str
    gamma_vol: float | None
    vanna_vol: float | None
    charm_vol: float | None


@dataclass(frozen=True)
class ResearchTicker:
    """A ticker surfaced from ingested company research, with its rationale."""

    symbol: str
    sentiment: float | None
    confidence: float | None
    themes: str
    rationale: str
    source_doc_id: str | None


@dataclass(frozen=True)
class TickerRegime:
    """Per-symbol regime row (metrics + flow + research flag) for the table."""

    symbol: str
    is_research: bool
    spot: float | None
    gex_total: float | None
    gex_dir: str
    gex_chg_wk: float | None
    gamma_regime: str
    gex_flip: float | None
    atm_iv: float | None
    call_put_oi: float | None
    vol_oi: float | None
    skew: float | None
    call_wall: float | None
    put_wall: float | None
    flow_tilt: str | None
    net_premium: float | None
    put_call_ratio: float | None


@dataclass(frozen=True)
class AmContext:
    """Everything the AM report renders, derived purely from stored data."""

    as_of: date
    market: list[MarketRead] = field(default_factory=list)
    research: list[ResearchTicker] = field(default_factory=list)
    watchlist: list[TickerRegime] = field(default_factory=list)
    static_symbols: list[str] = field(default_factory=list)
    research_symbols: list[str] = field(default_factory=list)


# ── Context builder ────────────────────────────────────────────────────


def _opt_f(value: object) -> float | None:
    num = pd.to_numeric(value, errors="coerce")
    return float(num) if pd.notna(num) else None


def _records(frame: pd.DataFrame) -> list[dict]:
    if frame is None or frame.empty:
        return []
    return frame.to_dict("records")


def _first_per_symbol(frame: pd.DataFrame) -> dict[str, dict]:
    """Map symbol -> first record (frames are newest-first where it matters)."""
    out: dict[str, dict] = {}
    for rec in _records(frame):
        sym = str(rec.get("symbol", "")).upper()
        if sym and sym not in out:
            out[sym] = rec
    return out


def _market_read(symbol: str, metric: dict, session: Session, as_of: date) -> MarketRead:
    series = load_intraday_flow_series(session, symbol, day=as_of)
    last = series.iloc[-1].to_dict() if series is not None and not series.empty else {}
    return MarketRead(
        symbol=symbol,
        spot=_opt_f(metric.get("spot")),
        gex_total=_opt_f(metric.get("gex_total")),
        gamma_regime=str(metric.get("gamma_regime") or "n/a"),
        atm_iv=_opt_f(metric.get("atm_iv")),
        gex_dir=str(metric.get("gex_dir") or "n/a"),
        gamma_vol=_opt_f(last.get("gamma_vol")),
        vanna_vol=_opt_f(last.get("vanna_vol")),
        charm_vol=_opt_f(last.get("charm_vol")),
    )


def build_am_context(
    session: Session, settings: Settings, *, as_of: date | None = None
) -> AmContext:
    """Assemble the AM report context from stored data (no live vendor pulls)."""
    as_of = as_of or eastern_now().date()
    symbols = effective_symbols(session, settings)
    static_set = set(settings.watchlist_symbols)

    metrics = load_watchlist_metrics(session, symbols)
    flow = load_watchlist_flow(session, symbols)
    entries = load_watchlist_entries(session, active_only=True)

    metric_by_sym = _first_per_symbol(metrics)
    flow_by_sym = _first_per_symbol(flow)
    entry_by_sym = _first_per_symbol(entries)
    research_set = {s for s in entry_by_sym}

    watchlist: list[TickerRegime] = []
    for sym in symbols:
        m = metric_by_sym.get(sym, {})
        fl = flow_by_sym.get(sym, {})
        watchlist.append(
            TickerRegime(
                symbol=sym,
                is_research=sym in research_set,
                spot=_opt_f(m.get("spot")),
                gex_total=_opt_f(m.get("gex_total")),
                gex_dir=str(m.get("gex_dir") or "n/a"),
                gex_chg_wk=_opt_f(m.get("gex_chg_wk")),
                gamma_regime=str(m.get("gamma_regime") or "n/a"),
                gex_flip=_opt_f(m.get("gex_flip")),
                atm_iv=_opt_f(m.get("atm_iv")),
                call_put_oi=_opt_f(m.get("call_put_oi")),
                vol_oi=_opt_f(m.get("vol_oi")),
                skew=_opt_f(m.get("skew")),
                call_wall=_opt_f(m.get("call_wall")),
                put_wall=_opt_f(m.get("put_wall")),
                flow_tilt=str(fl["tilt"]) if fl.get("tilt") is not None else None,
                net_premium=_opt_f(fl.get("net_premium")),
                put_call_ratio=_opt_f(fl.get("put_call_ratio")),
            )
        )

    market = [
        _market_read(sym, metric_by_sym.get(sym, {}), session, as_of)
        for sym in _INDEX_SYMBOLS
        if sym in symbols
    ]

    research = [
        ResearchTicker(
            symbol=str(rec.get("symbol", "")).upper(),
            sentiment=_opt_f(rec.get("sentiment")),
            confidence=_opt_f(rec.get("confidence")),
            themes=str(rec.get("themes") or ""),
            rationale=str(rec.get("rationale") or ""),
            source_doc_id=str(rec.get("source_doc_id"))
            if rec.get("source_doc_id") is not None
            else None,
        )
        for rec in _records(entries)
    ]

    return AmContext(
        as_of=as_of,
        market=market,
        research=research,
        watchlist=watchlist,
        static_symbols=[s for s in symbols if s in static_set],
        research_symbols=sorted(research_set),
    )


# ── Deterministic markdown (also the LLM-down fallback) ────────────────


def _f_num(value: float | None, *, nd: int = 2) -> str:
    return f"{value:,.{nd}f}" if value is not None else "—"


def _f_pct(value: float | None) -> str:
    return f"{value * 100:.1f}%" if value is not None else "—"


def _f_big(value: float | None) -> str:
    if value is None:
        return "—"
    a = abs(value)
    for unit, scale in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if a >= scale:
            return f"{value / scale:,.2f}{unit}"
    return f"{value:,.0f}"


def _market_section(ctx: AmContext) -> str:
    lines = ["## Market regime (SPX / SPY / QQQ)", ""]
    if not ctx.market:
        lines.append("_No index regime data collected yet._")
        return "\n".join(lines)
    for m in ctx.market:
        intraday = ""
        if any(v is not None for v in (m.gamma_vol, m.vanna_vol, m.charm_vol)):
            intraday = (
                f" 0DTE cumulative Γ {_f_big(m.gamma_vol)}, "
                f"vanna {_f_big(m.vanna_vol)}, charm {_f_big(m.charm_vol)}."
            )
        lines.append(
            f"- **{m.symbol}** — spot {_f_num(m.spot)}, net GEX {_f_big(m.gex_total)} "
            f"({m.gex_dir}), {m.gamma_regime}, ATM IV {_f_pct(m.atm_iv)}.{intraday}"
        )
    return "\n".join(lines)


def _research_section(ctx: AmContext) -> str:
    lines = ["## Research watchlist", ""]
    if not ctx.research:
        lines.append("_No research-surfaced tickers active._")
        return "\n".join(lines)
    for r in ctx.research:
        sent = f"sentiment {r.sentiment:+.2f}" if r.sentiment is not None else "sentiment —"
        conf = f", confidence {r.confidence:.2f}" if r.confidence is not None else ""
        themes = f" _[{r.themes}]_" if r.themes else ""
        rationale = r.rationale or "(no rationale captured)"
        lines.append(f"- **{r.symbol}** ({sent}{conf}) — {rationale}{themes}")
    return "\n".join(lines)


def _watchlist_table(ctx: AmContext) -> str:
    header = (
        "## Watchlist regime\n\n"
        "| Symbol | Spot | Net GEX | Dir | ΔGEX wk | Gamma regime | ATM IV | "
        "C/P OI | Skew | Flow tilt | Net prem |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|"
    )
    rows = [header]
    for t in ctx.watchlist:
        sym = f"{t.symbol}\\*" if t.is_research else t.symbol
        rows.append(
            f"| {sym} | {_f_num(t.spot)} | {_f_big(t.gex_total)} | {t.gex_dir} | "
            f"{_f_big(t.gex_chg_wk)} | {t.gamma_regime} | {_f_pct(t.atm_iv)} | "
            f"{_f_num(t.call_put_oi)} | {_f_num(t.skew, nd=3)} | "
            f"{t.flow_tilt or '—'} | {_f_big(t.net_premium)} |"
        )
    rows.append("")
    rows.append("\\* surfaced from company research.")
    return "\n".join(rows)


def build_tables_markdown(ctx: AmContext) -> str:
    """Deterministic markdown body (market + research + watchlist tables)."""
    return "\n\n".join(
        [_market_section(ctx), _research_section(ctx), _watchlist_table(ctx)]
    )


def render_am_markdown_fallback(ctx: AmContext) -> str:
    """Tables-only report used when the LLM is unavailable."""
    return (
        f"# AM Report — {ctx.as_of.isoformat()}\n\n"
        f"_LLM narrative unavailable; deterministic regime tables below._\n\n"
        f"{build_tables_markdown(ctx)}\n\n{_RULE4_NOTE}\n"
    )


# ── LLM-narrated render ────────────────────────────────────────────────


def render_am_markdown(
    ctx: AmContext, llm: LLMProvider, settings: Settings, *, model: str | None = None
) -> tuple[str, dict]:
    """Render the AM report markdown + metadata.

    Asks the LLM for a regime narrative grounded in the deterministic tables,
    then appends the tables as the data section. If the LLM call fails (e.g.
    Ollama is down), returns the deterministic tables-only fallback so the daily
    job still writes a row.
    """
    used_model = model or settings.LLM_DAILY_MODEL
    tables = build_tables_markdown(ctx)
    prompt = AM_SUMMARY_PROMPT.format(as_of=ctx.as_of.isoformat(), data=tables)

    narrative = ""
    used_llm = False
    try:
        narrative = llm.complete(prompt, model=used_model, max_tokens=900).strip()
        used_llm = bool(narrative)
    except Exception as exc:  # degrade to the deterministic fallback if the LLM errors
        log.warning("am_summary.llm_failed", error=str(exc))

    if used_llm:
        markdown = (
            f"# AM Report — {ctx.as_of.isoformat()}\n\n{narrative}\n\n"
            f"---\n\n## Data\n\n{tables}\n\n{_RULE4_NOTE}\n"
        )
    else:
        markdown = render_am_markdown_fallback(ctx)

    metadata = {
        "as_of": ctx.as_of.isoformat(),
        "used_llm": used_llm,
        "model": used_model if used_llm else None,
        "n_symbols": len(ctx.watchlist),
        "research_symbols": ctx.research_symbols,
    }
    return markdown, metadata
