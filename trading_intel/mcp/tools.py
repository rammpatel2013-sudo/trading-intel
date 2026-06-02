"""MCP tool functions — pure adapters over the existing data layer.

Each function takes a ``Session`` (and where relevant, an ``LLMProvider``)
plus its arguments and returns a JSON-serialisable dict. FastMCP decoration
is applied in ``server.py``; keeping the bodies plain makes them trivially
unit-testable with a SQLite ``Session`` + the existing ``StubLLM`` pattern.

Tools are READ-ONLY by design (FlashAlpha rule 4 + rule 1 spirit). They
never call ConvexValue, never write to ``signals``, and never persist
anything. Anything stored already came in via a scheduled job.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import structlog
from sqlalchemy.orm import Session

from trading_intel.config import Settings
from trading_intel.dashboard.am_report_data import (
    am_summary_by_date,
    available_dates,
    latest_am_summary,
)
from trading_intel.dashboard.flow_data import load_watchlist_flow
from trading_intel.dashboard.watchlist_metrics import load_watchlist_metrics
from trading_intel.memory.retrieval import retrieve_chunks
from trading_intel.synthesis.am_summary import build_am_context, render_am_markdown
from trading_intel.synthesis.llm import LLMProvider
from trading_intel.watchlist import effective_symbols

log = structlog.get_logger(__name__)

_DEFAULT_HISTORY_DAYS = 30
_DEFAULT_WEEKLY_DAYS = 7
_MAX_K = 20


def _normalise_symbols(
    session: Session, symbols: list[str] | None, settings: Settings
) -> list[str]:
    """Default to the effective watchlist; uppercase + dedupe what the caller passed."""
    if not symbols:
        return effective_symbols(session, settings)
    seen: set[str] = set()
    out: list[str] = []
    for s in symbols:
        u = s.strip().upper()
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def get_latest_am_summary(session: Session) -> dict[str, Any]:
    """Return the most recent stored AM regime report (markdown + metadata).

    Reads ``am_summaries`` only; never re-runs the LLM. Use
    ``rebuild_am_summary`` for that.
    """
    row = latest_am_summary(session)
    if row is None:
        return {"date": None, "markdown": None, "metadata": None, "found": False}
    return {
        "date": row.date.isoformat(),
        "markdown": row.markdown,
        "metadata": row.metadata_json,
        "found": True,
    }


def get_am_summary_by_date(session: Session, day: str) -> dict[str, Any]:
    """Return the AM report for an ISO date string (``YYYY-MM-DD``)."""
    try:
        d = date.fromisoformat(day)
    except ValueError as exc:
        return {"error": f"invalid date: {exc}", "found": False}
    row = am_summary_by_date(session, d)
    if row is None:
        return {"date": day, "found": False, "markdown": None}
    return {
        "date": row.date.isoformat(),
        "markdown": row.markdown,
        "metadata": row.metadata_json,
        "found": True,
    }


def list_am_summary_dates(session: Session, limit: int = 30) -> dict[str, Any]:
    """Return up to ``limit`` recent AM-report dates (newest first)."""
    limit = max(1, min(int(limit), 365))
    dates = available_dates(session)[:limit]
    return {"dates": [d.isoformat() for d in dates], "count": len(dates)}


def get_watchlist_regime(
    session: Session,
    symbols: list[str] | None = None,
    *,
    settings: Settings,
    history_days: int = _DEFAULT_HISTORY_DAYS,
    weekly_days: int = _DEFAULT_WEEKLY_DAYS,
) -> dict[str, Any]:
    """Watchlist regime-descriptor table (one row per symbol).

    Thin wrapper over ``dashboard.watchlist_metrics.load_watchlist_metrics``.
    Descriptors only — never a signal (FlashAlpha rule 4).
    """
    syms = _normalise_symbols(session, symbols, settings)
    df = load_watchlist_metrics(
        session, syms, weekly_days=int(weekly_days), history_days=int(history_days)
    )
    if df is None or df.empty:
        return {"symbols": syms, "rows": [], "count": 0}
    rows = df.to_dict(orient="records")
    return {"symbols": syms, "rows": rows, "count": len(rows)}


def get_watchlist_flow(
    session: Session,
    symbols: list[str] | None = None,
    *,
    settings: Settings,
) -> dict[str, Any]:
    """Latest flow-snapshot row per symbol (descriptive)."""
    syms = _normalise_symbols(session, symbols, settings)
    df = load_watchlist_flow(session, syms)
    if df is None or df.empty:
        return {"symbols": syms, "rows": [], "count": 0}
    rows = df.to_dict(orient="records")
    return {"symbols": syms, "rows": rows, "count": len(rows)}


def rebuild_am_summary(
    session: Session,
    llm: LLMProvider,
    *,
    settings: Settings,
) -> dict[str, Any]:
    """Re-render today's AM summary against current stored data (no DB write).

    Returns the same markdown + metadata pair the scheduler job produces, but
    does NOT upsert into ``am_summaries`` — that's the scheduled job's
    responsibility (idempotent ON CONFLICT, rule 5). Use this for ad-hoc
    "what would the brief look like right now?" queries from Claude Desktop.
    """
    ctx = build_am_context(session, settings)
    markdown, metadata = render_am_markdown(ctx, llm, settings)
    return {
        "date": ctx.as_of.isoformat(),
        "symbols": ctx.watchlist,
        "research_tickers": [r.symbol for r in ctx.research],
        "markdown": markdown,
        "metadata": metadata,
    }


def search_knowledge(
    session: Session,
    llm: LLMProvider,
    query: str,
    *,
    kind: str = "methodology",
    k: int = 6,
    symbols: list[str] | None = None,
) -> dict[str, Any]:
    """Semantic search over the PDF/docx knowledge base (pgvector).

    ``kind`` is ``methodology`` (desk frameworks) or ``research`` (company
    material). ``symbols`` restricts to chunks tagged with any of the given
    tickers — only meaningful for ``research``.
    """
    k_clamped = max(1, min(int(k), _MAX_K))
    if kind not in {"methodology", "research"}:
        return {"error": f"invalid kind: {kind!r}", "hits": []}
    syms = [s.strip().upper() for s in (symbols or []) if s and s.strip()]
    hits = retrieve_chunks(session, llm, query, k=k_clamped, kind=kind, symbols=syms or None)
    return {
        "query": query,
        "kind": kind,
        "k": k_clamped,
        "symbols": syms or None,
        "hits": [
            {
                "chunk_id": h.chunk_id,
                "document_id": h.document_id,
                "title": h.title,
                "text": h.text,
                "distance": h.distance,
            }
            for h in hits
        ],
        "count": len(hits),
    }
