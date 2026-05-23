"""Tests for the intraday-flow dashboard DB readers (SQLite)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.dashboard.ticker_data import (
    intraday_by_strike,
    load_intraday_flow_series,
    load_latest_intraday_flow,
)
from trading_intel.memory.models import IntradayFlow


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    IntradayFlow.__table__.create(engine)
    with Session(engine) as s:
        yield s


def _row(ts: datetime, strike: float, cp: str, gamma_vol: float, **kw) -> IntradayFlow:
    return IntradayFlow(
        symbol="SPX", ts=ts, source="convex", expiry=date(2026, 5, 22), dte=0,
        strike=strike, cp=cp, spot=5000.0, gamma_vol=gamma_vol,
        vanna_vol=kw.get("vanna_vol", 0.0), charm_vol=kw.get("charm_vol", 0.0),
        volume=kw.get("volume", 100),
    )


def test_load_latest_intraday_flow_picks_newest_ts(session: Session):
    t1 = datetime(2026, 5, 22, 14, 0, tzinfo=UTC)
    t2 = datetime(2026, 5, 22, 14, 5, tzinfo=UTC)
    session.add_all([_row(t1, 5000.0, "C", 1.0), _row(t2, 5000.0, "C", 9.0)])
    session.commit()
    ts, frame = load_latest_intraday_flow(session, "SPX")
    assert ts.replace(tzinfo=None) == t2.replace(tzinfo=None)
    assert len(frame) == 1
    assert frame["gamma_vol"].iloc[0] == pytest.approx(9.0)


def test_intraday_by_strike_sums_sides(session: Session):
    t = datetime(2026, 5, 22, 14, 5, tzinfo=UTC)
    session.add_all(
        [
            _row(t, 5000.0, "C", 5.0, volume=100),
            _row(t, 5000.0, "P", -2.0, volume=40),
            _row(t, 5010.0, "C", 3.0, volume=10),
        ]
    )
    session.commit()
    _, frame = load_latest_intraday_flow(session, "SPX")
    out = intraday_by_strike(frame).set_index("strike")
    assert out.loc[5000.0, "gamma_vol"] == pytest.approx(3.0)  # 5 + (-2)
    assert out.loc[5000.0, "volume"] == pytest.approx(140)
    assert out.loc[5010.0, "gamma_vol"] == pytest.approx(3.0)


def test_load_intraday_flow_series_aggregates_per_ts(session: Session):
    t1 = datetime(2026, 5, 22, 14, 0, tzinfo=UTC)
    t2 = datetime(2026, 5, 22, 14, 5, tzinfo=UTC)
    session.add_all(
        [
            _row(t1, 5000.0, "C", 2.0),
            _row(t1, 5010.0, "C", 3.0),  # t1 total gamma_vol = 5
            _row(t2, 5000.0, "C", 10.0),  # t2 total = 10
        ]
    )
    session.commit()
    series = load_intraday_flow_series(session, "SPX")
    assert list(series["gamma_vol"]) == [pytest.approx(5.0), pytest.approx(10.0)]
    # ascending by ts
    assert list(series["ts"]) == sorted(series["ts"])


def test_readers_empty(session: Session):
    ts, frame = load_latest_intraday_flow(session, "NOPE")
    assert ts is None and frame.empty
    assert load_intraday_flow_series(session, "NOPE").empty
    assert intraday_by_strike(frame).empty
