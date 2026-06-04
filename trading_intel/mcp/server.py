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
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session, sessionmaker

from trading_intel.config import Settings, get_settings
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
    def get_oi_changes(
        symbol: str, dte_max: int = 60, top: int = 15
    ) -> dict[str, Any]:
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
    def get_vol_richness(
        symbols: list[str] | None = None, horizon_dte: int = 30
    ) -> dict[str, Any]:
        """Latest IV-vs-forecast-RV richness scan per symbol (VRP percentile, rich/cheap)."""
        with session_factory() as session:
            return et.get_vol_richness(
                session, symbols, settings=settings, horizon_dte=horizon_dte
            )

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
    def get_research_watchlist(
        active_only: bool = True, limit: int = 200
    ) -> dict[str, Any]:
        """Research-driven watchlist: tickers surfaced from uploaded reports + rationale."""
        with session_factory() as session:
            return et.get_research_watchlist(
                session, active_only=active_only, limit=limit
            )

    @mcp.tool()
    def get_signals(
        symbol: str | None = None, days: int = 30, limit: int = 100
    ) -> dict[str, Any]:
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

