"""Tests for the AM-report dashboard read helpers."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.dashboard.am_report_data import (
    am_summary_by_date,
    available_dates,
    latest_am_summary,
)
from trading_intel.memory.models import AmSummary


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    AmSummary.__table__.create(engine)
    with Session(engine) as s:
        s.add_all(
            [
                AmSummary(date=date(2026, 5, 21), markdown="older", metadata_json={}),
                AmSummary(
                    date=date(2026, 5, 23),
                    markdown="newest",
                    metadata_json={"used_llm": True},
                ),
                AmSummary(date=date(2026, 5, 22), markdown="middle", metadata_json={}),
            ]
        )
        s.commit()
        yield s


def test_available_dates_newest_first(session: Session):
    assert available_dates(session) == [date(2026, 5, 23), date(2026, 5, 22), date(2026, 5, 21)]


def test_latest_returns_newest(session: Session):
    latest = latest_am_summary(session)
    assert latest is not None
    assert latest.date == date(2026, 5, 23)
    assert latest.markdown == "newest"


def test_by_date_returns_specific(session: Session):
    row = am_summary_by_date(session, date(2026, 5, 22))
    assert row is not None
    assert row.markdown == "middle"


def test_by_date_missing_returns_none(session: Session):
    assert am_summary_by_date(session, date(2026, 1, 1)) is None


def test_latest_empty_returns_none():
    engine = create_engine("sqlite://")
    AmSummary.__table__.create(engine)
    with Session(engine) as s:
        assert latest_am_summary(s) is None
        assert available_dates(s) == []
