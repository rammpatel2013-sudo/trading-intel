"""Reader for stored per-ticker research notes (Research Watchlist page).

Thin Session query over ``research_notes`` (written nightly by the research-note
job). Descriptive read-through only (FlashAlpha rule 4).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.memory.models import ResearchNote


def latest_research_note(session: Session, symbol: str) -> ResearchNote | None:
    """Most recent stored research note for ``symbol`` (or None)."""
    return session.execute(
        select(ResearchNote)
        .where(ResearchNote.symbol == symbol)
        .order_by(ResearchNote.as_of.desc())
        .limit(1)
    ).scalar_one_or_none()
