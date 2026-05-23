"""Readers for the research-driven dynamic watchlist (``watchlist_entries``)."""

from __future__ import annotations

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.memory.models import WatchlistEntry


def load_watchlist_entries(session: Session, *, active_only: bool = True) -> pd.DataFrame:
    """Research watchlist entries as a frame, newest first.

    Columns: ``symbol, sentiment, confidence, themes, rationale, source_doc_id,
    added_at, active``. Empty frame when nothing has been ingested.
    """
    stmt = select(WatchlistEntry)
    if active_only:
        stmt = stmt.where(WatchlistEntry.active.is_(True))
    rows = list(session.execute(stmt.order_by(WatchlistEntry.added_at.desc())).scalars())
    frame = pd.DataFrame(
        [
            {
                "symbol": r.symbol,
                "sentiment": r.sentiment,
                "confidence": r.confidence,
                "themes": ", ".join(r.themes) if r.themes else "",
                "rationale": r.rationale or "",
                "source_doc_id": r.source_doc_id,
                "added_at": r.added_at,
                "active": r.active,
            }
            for r in rows
        ]
    )
    return frame


def distinct_symbols(entries: pd.DataFrame) -> list[str]:
    """Unique symbols in the entries frame, preserving first-seen order."""
    if entries is None or entries.empty or "symbol" not in entries.columns:
        return []
    seen: list[str] = []
    for sym in entries["symbol"]:
        if sym not in seen:
            seen.append(sym)
    return seen
