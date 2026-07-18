"""FastMCP server — exposes the trading-intel read layer to Claude Desktop.

Composition root: instantiates ``Settings``, the session factory, and the
local ``OllamaProvider`` once, then registers the tools from
``trading_intel.mcp.tools`` against those shared resources.

The server runs locally (STDIO transport by default — what Claude Desktop
wants). Each tool call opens a short-lived session via the session
factory; nothing is held across calls.

Manual run:
    python -m trading_intel.mcp.server
    trading-intel-mcp                       # via the project script
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session, sessionmaker

from trading_intel.config import Settings, get_settings
from trading_intel.mcp import em_tools as em
from trading_intel.mcp import extra_tools as et
from trading_intel.mcp import tools as t
from trading_intel.memory.db import make_session_factory
from trading_intel.synthesis.llm import LLMProvider, OllamaProvider

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from trading_intel.clients import OptionsDataSource


def build_server(
    settings: Settings | None = None,
    *,
    llm: LLMProvider | None = None,
    session_factory: sessionmaker[Session] | None = None,
    source: OptionsDataSource | None = None,
) -> FastMCP:
    """Construct the FastMCP server with tools wired against shared resources.

    Arguments are injectable so tests can hand in a SQLite session factory and
    a stub ``LLMProvider`` without touching real Ollama or Postgres.
    """
    from fastmcp import FastMCP  # local import keeps fastmcp out of test collection

    settings = settings or get_settings()
    llm = llm or OllamaProvider(settings)
    session_factory = session_factory or make_session_factory(settings)

    mcp = FastMCP(name="trading-intel")

    # Lazy live-data source: only logs into Convex when a live tool (time & sales)
    # is actually called, so the DB-backed tools still work if Convex is down and
    # the server never blocks on a vendor login at startup.
    _src_cache: dict[str, OptionsDataSource] = {}

    def get_source() -> OptionsDataSource:
        if "src" not in _src_cache:
            if source is not None:
                _src_cache["src"] = source
            else:
                from trading_intel.clients.convex import ConvexClient

                _src_cache["src"] = ConvexClient(settings)
        return _src_cache["src"]

    @mcp.tool()
    def get_latest_am_summary() -> dict[str, Any]:
        """Return the most recent stored AM regime report."""
        with session_factory() as session:
            return t.get_latest_am_summary(session)

    @mcp.tool()
    def get_am_summary_by_date(day: str) -> dict[str, Any]:
        """Return the AM regime report for a given ISO date (YYYY-MM-DD)."""
        with session_factory() as session:
            return t.get_am_summary_by_date(session, day)

    @mcp.tool()
    def list_am_summary_dates(limit: int = 30) -> dict[str, Any]:
        """List recent AM-report dates (newest first)."""
        with session_factory() as session:
            return t.list_am_summary_dates(session, limit=limit)

    @mcp.tool()
    def get_watchlist_regime(
        symbols: list[str] | None = None,
        history_days: int = 30,
        weekly_days: int = 7,
    ) -> dict[str, Any]:
        """Watchlist regime-descriptor table. Descriptors only — never a signal."""
        with session_factory() as session:
            return t.get_watchlist_regime(
                session,
                symbols,
                settings=settings,
                history_days=history_days,
                weekly_days=weekly_days,
            )

    @mcp.tool()
    def get_watchlist_flow(symbols: list[str] | None = None) -> dict[str, Any]:
        """Latest options-flow snapshot per symbol (descriptive)."""
        with session_factory() as session:
            return t.get_watchlist_flow(session, symbols, settings=settings)

    # ── EM-break / gamma burn-off system (McGraw pattern) ──────────────
    @mcp.tool()
    def get_earnings_calendar(symbol: str | None = None, days: int = 30) -> dict[str, Any]:
        """Upcoming earnings dates (banked earn_cal). Anchors the EM-break system."""
        with session_factory() as session:
            return em.get_earnings_calendar(session, symbol, days=days)

    @mcp.tool()
    def get_em_break(symbol: str) -> dict[str, Any]:
        """How far the last earnings gap broke the pre-earnings expected move."""
        with session_factory() as session:
            return em.get_em_break(session, symbol)

    @mcp.tool()
    def get_gamma_burnoff(symbol: str) -> dict[str, Any]:
        """Front-expiry gamma share, decay, phase + OPEX countdown (descriptive)."""
        with session_factory() as session:
            return em.get_gamma_burnoff(session, symbol)

    @mcp.tool()
    def get_vol_control_flow(index: str = "SPY", window: int = 21) -> dict[str, Any]:
        """Vol-control (target-vol) buying pressure from the index RV roll-off."""
        with session_factory() as session:
            return em.get_vol_control_flow(session, index, window=window)

    @mcp.tool()
    def get_systematic_flow(index: str | None = None) -> dict[str, Any]:
        """Aggregate systematic (vol-control + CTA + risk-parity) buying across indices."""
        with session_factory() as session:
            return em.get_systematic_flow(session, index)

    @mcp.tool()
    def rebuild_am_summary() -> dict[str, Any]:
        """Re-render today's AM summary from current stored data. No DB write."""
        with session_factory() as session:
            return t.rebuild_am_summary(session, llm, settings=settings)

    @mcp.tool()
    def search_knowledge(
        query: str,
        kind: str = "methodology",
        k: int = 6,
        symbols: list[str] | None = None,
    ) -> dict[str, Any]:
        """Semantic search over the PDF/docx knowledge base (pgvector)."""
        with session_factory() as session:
            return t.search_knowledge(session, llm, query, kind=kind, k=k, symbols=symbols)

    @mcp.tool()
    def get_gamma_history(symbol: str, days: int = 30) -> dict[str, Any]:
        """Per-day net-GEX / flip / regime series for one ticker. Descriptors only."""
        with session_factory() as session:
            return t.get_gamma_history(session, symbol, days=days)

    @mcp.tool()
    def get_technicals(symbol: str, days: int = 120) -> dict[str, Any]:
        """Latest technical indicators (RSI/MACD/Bollinger/ATR) + candlestick patterns."""
        with session_factory() as session:
            return t.get_technicals(session, symbol, days=days)

    @mcp.tool()
    def get_skew_history(symbol: str, horizon_dte: int = 30, days: int = 60) -> dict[str, Any]:
        """25d risk-reversal (put IV - call IV) skew series for one ticker + 252d percentile."""
        with session_factory() as session:
            return t.get_skew_history(session, symbol, horizon_dte=horizon_dte, days=days)

    @mcp.tool()
    def render_report_html(symbol: str, days: int = 180) -> dict[str, Any]:
        """Write a standalone HTML chart (price + gamma levels + GEX + skew); return its path."""
        with session_factory() as session:
            return t.render_report_html(session, symbol, days=days)

    @mcp.tool()
    def get_time_and_sales(
        symbol: str | None = None, limit: int = 50, day: int = 0
    ) -> dict[str, Any]:
        """Biggest option prints from the MARKET-WIDE tape (or one root if symbol given).

        Sorted by premium; each row's ticker/strike/expiry is decoded from the
        contract symbol. Live only during market hours.
        """
        return t.get_time_and_sales(get_source(), symbol, limit=limit, day=day)

    # ── Per-strike positioning ─────────────────────────────────────────

    @mcp.tool()
    def get_walls(symbol: str, dte_max: int = 60) -> dict[str, Any]:
        """Call-wall / put-wall (top gamma-OI strikes) from the latest EOD chain."""
        with session_factory() as session:
            return et.get_walls(session, symbol, dte_max=dte_max)

    @mcp.tool()
    def get_straddle(symbol: str, dte_max: int = 400) -> dict[str, Any]:
        """ATM straddle price + expected-move range + day-over-day decay (latest EOD chain)."""
        with session_factory() as session:
            return et.get_straddle(session, symbol, dte_max=dte_max)

    @mcp.tool()
    def get_oi_changes(symbol: str, dte_max: int = 60, top: int = 15) -> dict[str, Any]:
        """Biggest day-over-day open-interest changes per strike (latest EOD snapshot)."""
        with session_factory() as session:
            return et.get_oi_changes(session, symbol, dte_max=dte_max, top=top)

    @mcp.tool()
    def get_gex_term(symbol: str) -> dict[str, Any]:
        """Rolling total GEX + per-expiration term structure for one ticker (latest EOD)."""
        with session_factory() as session:
            return et.get_gex_term(session, symbol)

    @mcp.tool()
    def get_live_gex(symbol: str) -> dict[str, Any]:
        """Latest intraday per-strike net GEX profile (live gamma map) for one ticker."""
        with session_factory() as session:
            return et.get_live_gex(session, symbol)

    @mcp.tool()
    def get_intraday_flow(symbol: str) -> dict[str, Any]:
        """Intraday 0DTE/1DTE volume-weighted gamma/delta/vanna/charm build for one ticker."""
        with session_factory() as session:
            return et.get_intraday_flow(session, symbol)

    @mcp.tool()
    def get_delta_flow(symbol: str, days: int = 5) -> dict[str, Any]:
        """Intraday cumulative call/put delta-notional series for one ticker."""
        with session_factory() as session:
            return et.get_delta_flow(session, symbol, days=days)

    # ── Vol complex ────────────────────────────────────────────────────

    @mcp.tool()
    def get_vol_richness(symbols: list[str] | None = None, horizon_dte: int = 30) -> dict[str, Any]:
        """Latest IV-vs-forecast-RV richness scan per symbol (VRP percentile, rich/cheap)."""
        with session_factory() as session:
            return et.get_vol_richness(session, symbols, settings=settings, horizon_dte=horizon_dte)

    @mcp.tool()
    def get_vix(days: int = 60) -> dict[str, Any]:
        """VIX complex daily series: VIX/VVIX/MOVE, credit OAS, term structure, VRP."""
        with session_factory() as session:
            return et.get_vix(session, days=days)

    @mcp.tool()
    def get_index_skew(days: int = 60) -> dict[str, Any]:
        """Index-level skew & VIX-decomposition series (Cboe SKEW, SDEX, tail-hedge score)."""
        with session_factory() as session:
            return et.get_index_skew(session, days=days)

    @mcp.tool()
    def get_iv_tenor(
        symbols: list[str] | None = None,
        tenor_dte: int | None = None,
        days: int = 90,
    ) -> dict[str, Any]:
        """Constant-maturity forward IV for index ETFs (QQQ/SPY/SPX).

        ATM IV + 15Δ/25Δ call/put wings at fixed 1M (30d) / 3M (90d) tenors, plus
        the derived 25Δ/15Δ risk reversals. ``symbols``/``tenor_dte`` optional
        filters; returns the daily series + the latest row per (symbol, tenor).
        """
        with session_factory() as session:
            return et.get_iv_tenor(session, symbols=symbols, tenor_dte=tenor_dte, days=days)

    @mcp.tool()
    def get_rv_rolloff(
        symbol: str = "SPY",
        window: int = 21,
        horizon: int = 10,
    ) -> dict[str, Any]:
        """Realized-vol roll-off projection: how trailing-window RV drifts as old days age out.

        Projects the trailing-``window`` (default 21d) realized vol forward
        ``horizon`` sessions on a calm-tape assumption, so you can see the
        mechanical RV floor that big past moves leave behind as they drop out of
        the window (Doc McGraw's "the June down-days age out mid-July → RV floor
        → launchpad for systematic buying"). ``symbol`` defaults to SPY
        (SPX quotes aren't refreshed daily). Descriptor only.
        """
        with session_factory() as session:
            return et.get_rv_rolloff(session, symbol=symbol, window=window, horizon=horizon)

    @mcp.tool()
    def get_vix_options() -> dict[str, Any]:
        """Latest stored EOD VIX-options chain (per expiry/strike/kind) + call OI share."""
        with session_factory() as session:
            return et.get_vix_options(session)

    # ── Research / narrative / signals ─────────────────────────────────

    @mcp.tool()
    def get_research_note(symbol: str) -> dict[str, Any]:
        """Latest narrative research note for one ticker (PDF + 10-K + FMP + regime)."""
        with session_factory() as session:
            return et.get_research_note(session, symbol)

    @mcp.tool()
    def get_surface_report(symbol: str) -> dict[str, Any]:
        """Latest interpretive vol-surface + flow report for one ticker."""
        with session_factory() as session:
            return et.get_surface_report(session, symbol)

    @mcp.tool()
    def get_research_watchlist(active_only: bool = True, limit: int = 200) -> dict[str, Any]:
        """Research-driven watchlist: tickers surfaced from uploaded reports + rationale."""
        with session_factory() as session:
            return et.get_research_watchlist(session, active_only=active_only, limit=limit)

    @mcp.tool()
    def get_flow_scorecard(
        lookback_days: int = 20,
        min_notional: float = 1_000_000.0,
        min_days: int = 2,
        limit: int = 40,
    ) -> dict[str, Any]:
        """Accumulation/distribution scorecard from the durable option-tape roll-up."""
        with session_factory() as session:
            return et.get_flow_scorecard(
                session,
                lookback_days=lookback_days,
                min_notional=min_notional,
                min_days=min_days,
                limit=limit,
            )

    @mcp.tool()
    def get_flow_report(
        lookback_days: int = 21,
        recent_days: int = 5,
        min_notional: float = 1_000_000.0,
        top: int = 25,
    ) -> dict[str, Any]:
        """Longitudinal option-flow report: accumulation trend, contract lifecycle, new/fading.

        Extends get_flow_scorecard with the *trend* dimension off the durable
        tape roll-up (tas_daily_flow + tas_daily_contract): who is building vs
        bailing (recent-vs-prior score + net-buy streak), which exact contracts
        are being accumulated over the window, and which names are newly on or
        dropping off the board. Descriptor only.
        """
        with session_factory() as session:
            return et.get_flow_report(
                session,
                lookback_days=lookback_days,
                recent_days=recent_days,
                min_notional=min_notional,
                top=top,
            )

    @mcp.tool()
    def get_flow_intelligence(symbol: str, trade_date: str | None = None) -> dict[str, Any]:
        """Flow-intelligence drill-in for one name from the option tape: net-premium
        4-way (call-buy / put-sell / put-buy / call-sell + a bullish-minus-bearish
        tilt), institutional-vs-retail split by print size, DTE-bucket premium, the
        accumulation summary, and the most-active strikes. buy/sell is the tape
        aggressor side (not an OI-change guess). trade_date is ISO YYYY-MM-DD; the
        latest tape day is used if omitted.
        """
        from datetime import date

        from trading_intel.mcp.flow_intelligence_tool import flow_intelligence

        td = date.fromisoformat(trade_date) if trade_date else None
        with session_factory() as session:
            return flow_intelligence(session, symbol, trade_date=td)

    @mcp.tool()
    def get_signals(symbol: str | None = None, days: int = 30, limit: int = 100) -> dict[str, Any]:
        """Recent rows from the validated signals table (read-only)."""
        with session_factory() as session:
            return et.get_signals(session, symbol, days=days, limit=limit)

    @mcp.tool()
    def generate_ticker_report(symbol: str, days: int = 180) -> dict[str, Any]:
        """Generate the full positioning/flow/technicals HTML report for one ticker.

        Builds the standard report (the same one `scripts/ticker_report.py`
        produces — cards, combined line view, price/skew panels, GEX term, Vol/OI
        + day-over-day positioning, TAS top prints) and returns the saved HTML
        path under ``reports/``. Descriptive only (FlashAlpha rule 4).
        """
        from trading_intel.reports import build

        return {"symbol": symbol.strip().upper(), "path": build(symbol, days=days), "found": True}

    @mcp.tool()
    def generate_vol_surface_report(symbol: str = "SPX") -> dict[str, Any]:
        """Generate the vol-surface *changes* HTML report for an index ETF (SPX/QQQ/SPY).

        Reads the banked ``surface_snapshots`` (near-money per-STRIKE × expiry IV grid), diffs
        today vs the prior banked day (fixed-strike: each listed contract vs its own prior mark),
        and reads the multi-day FIXED-STRIKE front-week vol *footprint* (the same call / put
        strikes offered or bid day after day) to infer dealer positioning — long gamma (street
        lightening) vs short gamma / crash bid — then cross-checks it against net GEX (confirm or
        contradict). Returns the saved HTML path under ``reports/`` (surface + 3D + changes + skew
        + term + a 'The read' footprint panel + a 'How to read this' legend). The footprint/changes
        need >=2 banked days. Descriptive only (FlashAlpha rule 4) — GEX is the model, the surface
        is the receipt.
        """
        from trading_intel.reports import build_vol_surface

        path = build_vol_surface(symbol)
        return {"symbol": symbol.strip().upper(), "path": path, "uri": Path(path).as_uri(), "found": True}

    @mcp.tool()
    def generate_eod_vol_report(days: int = 252) -> dict[str, Any]:
        """Generate the EOD volatility dashboard report (HTML) and return its path.

        Doc-style end-of-day vol read: a tabbed HTML report (Summary · Decomposition
        · Term Structure · VVIX/VIX · Rabbit Hole · COR1M Map) with plain-language
        day-over-day / week-over-week commentary and a 'what to expect next day /
        next week' forward read, built from stored data (VIX/VVIX/term, the 6-factor
        decomposition, Nations VolDex/SkewDex/TailDex, COR1M/COR3M, VIXEQ/DSPX and
        the dispersion spread). Returns the saved HTML path under ``reports/`` —
        open it to view/share. Descriptive only (FlashAlpha rule 4).
        """
        from trading_intel.reports import build_eod_vol

        path = build_eod_vol(days=days, llm=llm, settings=settings)
        return {"path": path, "uri": Path(path).as_uri(), "found": True}

    @mcp.tool()
    def generate_flow_report(
        lookback_days: int = 21, recent_days: int = 5, min_notional: float = 1_000_000.0
    ) -> dict[str, Any]:
        """Generate the EOD option-tape Flow Report (HTML) and return its path.

        Longitudinal accumulation/distribution off the durable roll-up tables: a
        Key-Findings callout (top accumulators + net-buy streaks, biggest
        single-contract builds, new vs fading names), plus leaderboards and the
        repeat-contract lifecycle. Returns the saved HTML path under ``reports/``.
        Descriptive only (FlashAlpha rule 4).
        """
        from trading_intel.reports import build_flow

        path = build_flow(
            lookback_days=lookback_days,
            recent_days=recent_days,
            min_notional=min_notional,
            llm=llm,
            settings=settings,
        )
        return {"path": path, "uri": Path(path).as_uri(), "found": True}

    return mcp


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.LOG_LEVEL)
    log = logging.getLogger("trading_intel.mcp")
    log.info("Starting trading-intel MCP server (env=%s)", settings.APP_ENV)
    mcp = build_server(settings)
    mcp.run()  # defaults to STDIO transport — what Claude Desktop expects


if __name__ == "__main__":
    main()
