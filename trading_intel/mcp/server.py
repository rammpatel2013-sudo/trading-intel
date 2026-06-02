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
from trading_intel.mcp import tools as t
from trading_intel.memory.db import make_session_factory
from trading_intel.synthesis.llm import LLMProvider, OllamaProvider

if TYPE_CHECKING:
    from fastmcp import FastMCP


def build_server(
    settings: Settings | None = None,
    *,
    llm: LLMProvider | None = None,
    session_factory: sessionmaker[Session] | None = None,
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

