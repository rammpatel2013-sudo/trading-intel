"""Tests for the dynamic-watchlist readers (SQLite)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.dashboard.dynamic_watchlist import distinct_symbols, load_watchlist_entries
from trading_intel.memory.models import WatchlistEntry


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    WatchlistEntry.__table__.create(engine)
    with Session(engine) as s:
        yield s


def test_load_entries_newest_first_and_active_filter(session: Session):
    session.add_all(
        [
            WatchlistEntry(symbol="NVDA", rationale="AI", sentiment=0.8, confidence=0.9,
                           themes=["AI capex"], added_at=datetime(2026, 5, 21, tzinfo=UTC),
                           active=True),
            WatchlistEntry(symbol="AMD", rationale="share gains", sentiment=0.3,
                           added_at=datetime(2026, 5, 22, tzinfo=UTC), active=True),
            WatchlistEntry(symbol="OLD", rationale="stale", active=False,
                           added_at=datetime(2026, 5, 1, tzinfo=UTC)),
        ]
    )
    session.commit()
    df = load_watchlist_entries(session)
    assert list(df["symbol"]) == ["AMD", "NVDA"]  # newest first, inactive excluded
    assert df.iloc[1]["themes"] == "AI capex"
    assert distinct_symbols(df) == ["AMD", "NVDA"]


def test_load_entries_includes_inactive_when_requested(session: Session):
    session.add(WatchlistEntry(symbol="OLD", added_at=datetime(2026, 5, 1, tzinfo=UTC),
                               active=False))
    session.commit()
    assert load_watchlist_entries(session).empty
    assert not load_watchlist_entries(session, active_only=False).empty


def test_distinct_symbols_empty():
    import pandas as pd
    assert distinct_symbols(pd.DataFrame()) == []
