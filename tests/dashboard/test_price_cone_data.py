"""Tests for the forward price-cone data layer (SQLite)."""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.dashboard.price_cone_data import build_cone, load_close_series
from trading_intel.memory.models import QuoteDaily


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    QuoteDaily.__table__.create(engine)
    with Session(engine) as s:
        yield s


def _seed_closes(session: Session, n: int = 60) -> None:
    d0 = date(2026, 1, 2)
    for i in range(n):
        # gentle oscillation so realized vol > 0
        close = 100.0 + 5.0 * math.sin(i / 3.0)
        session.add(QuoteDaily(
            symbol="SPX", date=d0 + timedelta(days=i),
            open=close, high=close + 1.0, low=close - 1.0, close=close, volume=1_000_000,
        ))
    session.commit()


def test_load_close_series(session: Session):
    _seed_closes(session, n=10)
    s = load_close_series(session, "SPX")
    assert len(s) == 10
    assert s.index.is_monotonic_increasing  # date-indexed ascending


def test_build_cone_uses_last_close_and_forecast_vol(session: Session):
    _seed_closes(session, n=60)
    ann_vol, anchor, cone = build_cone(session, "SPX", horizon_days=21)
    assert ann_vol is not None and ann_vol > 0
    assert anchor is not None and anchor > 0           # defaults to last close
    assert len(cone) == 21
    assert (cone["hi1"] > cone["median"]).all()


def test_build_cone_spot_override(session: Session):
    _seed_closes(session, n=60)
    _, anchor, cone = build_cone(session, "SPX", spot=4321.0, horizon_days=10)
    assert anchor == 4321.0
    assert cone.iloc[0]["median"] == 4321.0


def test_build_cone_no_data_empty(session: Session):
    ann_vol, anchor, cone = build_cone(session, "NOPE")
    assert ann_vol is None and anchor is None and cone.empty
