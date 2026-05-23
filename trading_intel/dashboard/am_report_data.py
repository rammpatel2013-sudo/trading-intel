"""Read helpers for the AM Report dashboard page.

Pure data access over ``am_summaries`` so the Streamlit page stays a thin shell
(and these stay unit-testable against an in-memory DB).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.memory.models import AmSummary


def available_dates(session: Session) -> list[date]:
    """All AM-report dates, newest first."""
    rows = session.execute(select(AmSummary.date).order_by(AmSummary.date.desc())).scalars()
    return list(rows)


def latest_am_summary(session: Session) -> AmSummary | None:
    """The most recent AM report, or None if none exist."""
    return session.execute(
        select(AmSummary).order_by(AmSummary.date.desc()).limit(1)
    ).scalar_one_or_none()


def am_summary_by_date(session: Session, day: date) -> AmSummary | None:
    """The AM report for a specific date, or None."""
    return session.execute(
        select(AmSummary).where(AmSummary.date == day)
    ).scalar_one_or_none()
