"""Tests for the Delta-Flow dashboard data layer (SQLite)."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.dashboard.delta_flow_data import (
    delta_flow_symbols,
    load_delta_flow_day,
)
from trading_intel.memory.models import DeltaFlow


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    DeltaFlow.__table__.create(engine)
    with Session(engine) as s:
        yield s


def _row(session: Session, **kw) -> None:
    base = dict(
        symbol="SPX", ts=datetime(2026, 5, 26, 10, 0), source="convex", spot=7500.0,
        next_expiry=date(2026, 5, 27), call_notional_all=6.5e8, put_notional_all=-4.0e8,
        call_notional_next=5.0e8, put_notional_next=-3.0e8,
    )
    base.update(kw)
    session.add(DeltaFlow(**base))


def test_load_empty():
    engine = create_engine("sqlite://")
    DeltaFlow.__table__.create(engine)
    with Session(engine) as s:
        assert load_delta_flow_day(s, "SPX").empty


def test_load_returns_only_latest_day_oldest_first(session: Session):
    _row(session, ts=datetime(2026, 5, 25, 15, 0))  # prior session
    _row(session, ts=datetime(2026, 5, 26, 9, 35))
    _row(session, ts=datetime(2026, 5, 26, 9, 30))
    session.commit()
    out = load_delta_flow_day(session, "SPX")
    assert len(out) == 2  # only 2026-05-26
    assert list(out["ts"]) == [
        datetime(2026, 5, 26, 9, 30), datetime(2026, 5, 26, 9, 35)
    ]  # oldest first
    assert out.iloc[0]["call_notional_all"] == pytest.approx(6.5e8)


def test_load_specific_day(session: Session):
    _row(session, ts=datetime(2026, 5, 25, 15, 0), spot=7400.0)
    _row(session, ts=datetime(2026, 5, 26, 9, 30), spot=7500.0)
    session.commit()
    out = load_delta_flow_day(session, "SPX", day=date(2026, 5, 25))
    assert list(out["spot"]) == [7400.0]


def test_delta_flow_symbols(session: Session):
    _row(session, symbol="SPX")
    _row(session, symbol="QQQ", ts=datetime(2026, 5, 26, 10, 5))
    session.commit()
    assert delta_flow_symbols(session) == ["QQQ", "SPX"]
