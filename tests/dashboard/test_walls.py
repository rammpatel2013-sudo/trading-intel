"""Tests for the wall-history loader/report — SQLite, no Postgres."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.dashboard.walls import build_wall_report, load_wall_history
from trading_intel.memory.models import GreeksChain


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    GreeksChain.__table__.create(engine)
    with Session(engine) as s:
        yield s


def _add_day(session: Session, ts: datetime, call_wall: float, put_wall: float) -> None:
    """Insert a snapshot whose call/put gxoi peaks at the given strikes."""
    rows = [
        (call_wall, "C", 50.0),
        (call_wall - 100, "C", 10.0),
        (put_wall, "P", 40.0),
        (put_wall + 100, "P", 8.0),
    ]
    for strike, cp, gxoi in rows:
        session.add(
            GreeksChain(symbol="SPX", ts=ts, expiry=ts.date(), strike=strike,
                        cp=cp, gxoi=gxoi, source="convex")
        )
    session.commit()


def test_load_wall_history_one_per_day_newest_first(session: Session):
    d1 = datetime(2026, 5, 21, 20, 15, tzinfo=UTC)
    d1b = datetime(2026, 5, 21, 14, 0, tzinfo=UTC)  # same day, earlier
    d2 = datetime(2026, 5, 22, 20, 15, tzinfo=UTC)
    _add_day(session, d1b, 7400, 7000)
    _add_day(session, d1, 7450, 7050)  # latest on the 21st -> this one wins
    _add_day(session, d2, 7500, 7100)

    hist = load_wall_history(session, "SPX", days=10)
    assert [h["date"].isoformat() for h in hist] == ["2026-05-22", "2026-05-21"]
    assert hist[0]["call_wall"] == 7500.0
    assert hist[1]["call_wall"] == 7450.0  # latest snapshot of the 21st, not 7400


def test_build_wall_report_shows_movement(session: Session):
    _add_day(session, datetime(2026, 5, 21, 20, 15, tzinfo=UTC), 7400, 7000)
    _add_day(session, datetime(2026, 5, 22, 20, 15, tzinfo=UTC), 7500, 7050)

    md = build_wall_report(session, "SPX")
    assert "## Call / put walls" in md
    assert "call wall **7500**" in md
    assert "up 100 from 7400" in md  # call wall moved up
    assert "up 50 from 7000" in md   # put wall moved up


def test_build_wall_report_single_day(session: Session):
    _add_day(session, datetime(2026, 5, 22, 20, 15, tzinfo=UTC), 7500, 7100)
    md = build_wall_report(session, "SPX")
    assert "call wall **7500**" in md
    assert ">= 2 days" in md  # movement note


def test_build_wall_report_no_data(session: Session):
    assert "No chain snapshots stored yet." in build_wall_report(session, "SPX")
