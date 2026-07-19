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


# ── Mutations (for the dashboard's manage UI — no terminal needed) ──────
# Thin wrappers over WatchlistEntry, mirroring scripts/add_watchlist.py so the
# dashboard and CLI stay behaviourally identical. effective_symbols unions active
# entries with the static WATCHLIST, so collectors pick up adds / drop removes on
# their next run (no image rebuild).


def active_symbols(session: Session) -> list[str]:
    """Distinct symbols with an active watchlist entry (for the remove picker)."""
    rows = (
        session.execute(
            select(WatchlistEntry.symbol)
            .where(WatchlistEntry.active.is_(True))
            .distinct()
            .order_by(WatchlistEntry.symbol)
        )
        .scalars()
        .all()
    )
    return list(rows)


def add_manual_entry(session: Session, symbol: str, *, rationale: str | None = None) -> str:
    """Add (or reactivate) a MANUAL research-watchlist entry (source_doc_id NULL)."""
    sym = symbol.strip().upper()
    if not sym:
        return "enter a ticker"
    existing = (
        session.execute(
            select(WatchlistEntry).where(
                WatchlistEntry.symbol == sym,
                WatchlistEntry.source_doc_id.is_(None),
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        existing.active = True
        if rationale:
            existing.rationale = rationale
        session.commit()
        return f"{sym}: reactivated"
    session.add(
        WatchlistEntry(
            symbol=sym,
            source_doc_id=None,
            rationale=rationale or "added from dashboard",
            sentiment=None,
            confidence=None,
            themes=None,
            active=True,
        )
    )
    session.commit()
    return f"{sym}: added"


def deactivate_symbol(session: Session, symbol: str) -> str:
    """Deactivate ALL active entries (manual + research) for ``symbol``."""
    sym = symbol.strip().upper()
    rows = list(
        session.execute(
            select(WatchlistEntry).where(
                WatchlistEntry.symbol == sym,
                WatchlistEntry.active.is_(True),
            )
        ).scalars()
    )
    if not rows:
        return f"{sym}: nothing active to remove"
    for r in rows:
        r.active = False
    session.commit()
    return f"{sym}: removed ({len(rows)} entr{'y' if len(rows) == 1 else 'ies'})"
