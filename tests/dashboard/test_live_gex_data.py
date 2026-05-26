"""Tests for the live-GEX dashboard loader (SQLite)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.dashboard.live_gex_data import live_spot, load_live_chain
from trading_intel.memory.models import LiveGex


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    LiveGex.__table__.create(engine)
    with Session(engine) as s:
        yield s


def _row(session: Session, ts: datetime, **kw) -> None:
    base = dict(
        symbol="SPX", ts=ts, strike=7500.0, cp="C", source="convex", spot=7500.0,
        delta=0.5, gamma=0.01, iv=0.2, gxoi=1e6, dxoi=2e6,
    )
    base.update(kw)
    session.add(LiveGex(**base))


def test_load_live_chain_fresh(session: Session):
    now = datetime(2026, 5, 26, 12, 0)
    _row(session, now - timedelta(minutes=5), strike=7500.0, cp="C")
    _row(session, now - timedelta(minutes=5), strike=7500.0, cp="P", gxoi=-8e5)
    session.commit()
    ts, frame = load_live_chain(session, "SPX", max_age_min=15, now=now)
    assert ts == now - timedelta(minutes=5)
    assert set(frame["opt_kind"]) == {"call", "put"}
    assert live_spot(frame) == 7500.0


def test_load_live_chain_stale_returns_none(session: Session):
    now = datetime(2026, 5, 26, 12, 0)
    _row(session, now - timedelta(minutes=40))  # older than max_age_min
    session.commit()
    ts, frame = load_live_chain(session, "SPX", max_age_min=15, now=now)
    assert ts is None and frame.empty


def test_load_live_chain_none_when_empty(session: Session):
    ts, frame = load_live_chain(session, "SPX", now=datetime(2026, 5, 26, 12, 0))
    assert ts is None and frame.empty
    assert live_spot(frame) is None


def test_load_live_chain_uses_only_latest_ts(session: Session):
    now = datetime(2026, 5, 26, 12, 0)
    _row(session, now - timedelta(minutes=12), strike=7400.0, cp="C")  # older slot
    _row(session, now - timedelta(minutes=2), strike=7500.0, cp="C")   # newest slot
    session.commit()
    ts, frame = load_live_chain(session, "SPX", max_age_min=15, now=now)
    assert ts == now - timedelta(minutes=2)
    assert list(frame["strike"]) == [7500.0]
