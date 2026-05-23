"""Tests for the flow dashboard readers (SQLite)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.dashboard.flow_data import load_latest_flow, load_watchlist_flow
from trading_intel.memory.models import FlowSnapshot


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    FlowSnapshot.__table__.create(engine)
    with Session(engine) as s:
        yield s


def _add(session: Session, symbol: str, ts: datetime, pcr: float) -> None:
    session.add(
        FlowSnapshot(symbol=symbol, ts=ts, source="convex", call_notional=3e6,
                     put_notional=1e6, net_premium=2e6, put_call_ratio=pcr,
                     tilt="offensive (call-heavy)", n_prints=2,
                     top_prints=[{"strike": 100.0, "premium": 3e6}], packages=[])
    )


def test_load_latest_flow_newest(session: Session):
    _add(session, "SPY", datetime(2026, 5, 22, 10, 0, tzinfo=UTC), 0.40)
    _add(session, "SPY", datetime(2026, 5, 22, 10, 30, tzinfo=UTC), 0.33)
    snap = load_latest_flow(session, "SPY")
    assert snap.put_call_ratio == pytest.approx(0.33)


def test_load_watchlist_flow_skips_missing(session: Session):
    _add(session, "SPY", datetime(2026, 5, 22, 10, 30, tzinfo=UTC), 0.33)
    df = load_watchlist_flow(session, ["SPY", "QQQ"])
    assert list(df["symbol"]) == ["SPY"]  # QQQ has no flow -> omitted
    assert df.iloc[0]["tilt"] == "offensive (call-heavy)"


def test_load_latest_flow_none(session: Session):
    assert load_latest_flow(session, "NOPE") is None
    assert load_watchlist_flow(session, ["NOPE"]).empty
