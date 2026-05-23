"""Tests for the fixed-strike-change and wall-drift chart loaders (SQLite)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.dashboard.changes import load_fixed_strike_changes
from trading_intel.dashboard.walls import wall_history_frame
from trading_intel.memory.models import GreeksChain

D21 = datetime(2026, 5, 21, 20, tzinfo=UTC)
D22 = datetime(2026, 5, 22, 20, tzinfo=UTC)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    GreeksChain.__table__.create(engine)
    with Session(engine) as s:
        yield s


def _add_day(session: Session, ts: datetime, *, iv_100: float, call_wall: float,
             put_wall: float) -> None:
    expiry = datetime(2026, 6, 19, tzinfo=UTC).date()
    rows = [
        # strike 100 call: iv drives the fixed-strike change; gxoi sets walls
        (100.0, "C", iv_100, 5.0),
        (call_wall, "C", 0.20, 50.0),
        (put_wall, "P", 0.30, 40.0),
    ]
    for strike, cp, iv, gxoi in rows:
        session.add(
            GreeksChain(symbol="SPY", ts=ts, expiry=expiry, strike=strike, cp=cp,
                        iv=iv, gxoi=gxoi, delta=0.5, source="convex")
        )
    session.commit()


def test_load_fixed_strike_changes_diffs_two_days(session: Session):
    _add_day(session, D21, iv_100=0.20, call_wall=105, put_wall=95)
    _add_day(session, D22, iv_100=0.23, call_wall=106, put_wall=95)
    changes = load_fixed_strike_changes(session, "SPY")
    assert changes is not None
    row = changes[(changes["strike"] == 100.0) & (changes["opt_kind"] == "C")].iloc[0]
    assert row["d_iv_pts"] == pytest.approx(3.0, abs=1e-6)  # (0.23-0.20)*100


def test_load_fixed_strike_changes_needs_two_days(session: Session):
    _add_day(session, D22, iv_100=0.23, call_wall=106, put_wall=95)
    assert load_fixed_strike_changes(session, "SPY") is None


def test_wall_history_frame_ascending(session: Session):
    _add_day(session, D21, iv_100=0.20, call_wall=105, put_wall=95)
    _add_day(session, D22, iv_100=0.23, call_wall=106, put_wall=94)
    frame = wall_history_frame(session, "SPY")
    assert list(frame["date"]) == sorted(frame["date"])
    assert frame["call_wall"].iloc[-1] == pytest.approx(106.0)
    assert frame["put_wall"].iloc[-1] == pytest.approx(94.0)


def test_wall_history_frame_empty(session: Session):
    assert wall_history_frame(session, "NOPE").empty
