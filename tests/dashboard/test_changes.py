"""Tests for the dashboard change-panel loader/report — SQLite, no Postgres."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.dashboard.changes import build_change_report, load_recent_chain_snapshots
from trading_intel.memory.models import GreeksChain


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    GreeksChain.__table__.create(engine)  # only this table (avoids ARRAY cols)
    with Session(engine) as s:
        yield s


def _add_snapshot(session: Session, ts: datetime, atm: float) -> None:
    """Insert a delta-rich one-expiry chain snapshot at a given ATM IV level."""
    expiry = date(2026, 6, 18)
    for d in (0.05, 0.1, 0.2, 0.3, 0.4, 0.45, 0.5):
        session.add(
            GreeksChain(symbol="SPX", ts=ts, expiry=expiry, strike=7400 + round(d * 1000),
                        cp="C", delta=d, iv=atm + (0.5 - d) * 0.1, source="convex")
        )
        session.add(
            GreeksChain(symbol="SPX", ts=ts, expiry=expiry, strike=7400 - round(d * 1000),
                        cp="P", delta=-d, iv=atm + (0.5 - d) * 0.12, source="convex")
        )
    session.commit()


def test_load_recent_returns_newest_first(session: Session):
    older = datetime(2026, 5, 21, 6, 45, tzinfo=UTC)
    newer = datetime(2026, 5, 22, 6, 45, tzinfo=UTC)
    _add_snapshot(session, older, 0.15)
    _add_snapshot(session, newer, 0.18)

    snaps = load_recent_chain_snapshots(session, "SPX", n=2)
    # newest first (compare tz-naive: SQLite drops tzinfo, Postgres keeps it)
    got = [ts.replace(tzinfo=None) for ts, _ in snaps]
    assert got == [newer.replace(tzinfo=None), older.replace(tzinfo=None)]
    assert set(snaps[0][1].columns) >= {"expiration", "opt_kind", "strike", "delta", "iv"}
    assert len(snaps[0][1]) == 14  # 7 calls + 7 puts


def test_build_change_report_two_snapshots(session: Session):
    _add_snapshot(session, datetime(2026, 5, 21, 6, 45, tzinfo=UTC), 0.15)
    _add_snapshot(session, datetime(2026, 5, 22, 6, 45, tzinfo=UTC), 0.18)

    md = build_change_report(session, "SPX")
    assert "## Day-over-day vol changes (2026-05-21 -> 2026-05-22)" in md
    assert "## Fixed-strike vol changes (sticky-strike)" in md
    assert "## ATM vol changes (sticky-delta)" in md
    assert "vol pts" in md


def test_build_change_report_needs_two(session: Session):
    _add_snapshot(session, datetime(2026, 5, 22, 6, 45, tzinfo=UTC), 0.15)
    md = build_change_report(session, "SPX")
    assert "Not enough history yet" in md


def test_build_change_report_no_data(session: Session):
    assert "Not enough history yet" in build_change_report(session, "SPX")
