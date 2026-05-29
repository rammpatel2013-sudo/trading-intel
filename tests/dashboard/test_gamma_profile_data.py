"""Tests for the MM gamma-profile data layer (SQLite)."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.dashboard.gamma_profile_data import (
    ALL,
    ZERO_DTE,
    available_expiries,
    build_profile,
    filter_scope,
    load_latest_chain,
    snapshot_spot,
)
from trading_intel.memory.models import LiveGex


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    LiveGex.__table__.create(engine)
    with Session(engine) as s:
        yield s


def _row(session: Session, **kw) -> None:
    base = dict(
        symbol="SPX", ts=datetime(2026, 5, 26, 15, 0), strike=7500.0, cp="C",
        expiry=date(2026, 5, 26), source="convex", spot=7475.0, iv=0.18, oi=5000.0,
    )
    base.update(kw)
    session.add(LiveGex(**base))


def test_load_latest_chain_shapes_and_spot(session: Session):
    _row(session, ts=datetime(2026, 5, 26, 10, 0), strike=7500.0)  # earlier snapshot
    _row(session, ts=datetime(2026, 5, 26, 15, 0), strike=7500.0, cp="C")
    _row(session, ts=datetime(2026, 5, 26, 15, 0), strike=7400.0, cp="P")
    session.commit()
    ts, frame = load_latest_chain(session, "SPX")
    assert ts == datetime(2026, 5, 26, 15, 0)
    assert len(frame) == 2  # only the latest snapshot
    assert set(frame["opt_kind"]) == {"call", "put"}
    assert snapshot_spot(frame) == 7475.0


def test_scope_filter_0dte_vs_all(session: Session):
    _row(session, strike=7500.0, cp="C", expiry=date(2026, 5, 26))  # 0DTE
    _row(session, strike=7500.0, cp="C", expiry=date(2026, 7, 17))  # far
    session.commit()
    _, frame = load_latest_chain(session, "SPX")
    assert available_expiries(frame) == [date(2026, 5, 26), date(2026, 7, 17)]
    ref = date(2026, 5, 26)
    assert len(filter_scope(frame, ALL, ref=ref)) == 2
    zdte = filter_scope(frame, ZERO_DTE, ref=ref)
    assert len(zdte) == 1
    assert pytest.approx(zdte.iloc[0]["strike"]) == 7500.0


def test_build_profile_per_expiry_plus_all(session: Session):
    _row(session, strike=7500.0, cp="C", expiry=date(2026, 5, 29), iv=0.18, oi=5000.0)
    _row(session, strike=7500.0, cp="C", expiry=date(2026, 7, 17), iv=0.20, oi=2000.0)
    session.commit()
    _, frame = load_latest_chain(session, "SPX")
    prof = build_profile(frame, 7475.0, scope=ALL, ref=date(2026, 5, 26), n_points=41)
    assert "all" in prof.columns
    expiry_cols = [c for c in prof.columns if c != "all"]
    assert expiry_cols == ["2026-05-29", "2026-07-17"]
    # 0DTE scope (no contract expires on the ref day) -> empty profile
    assert build_profile(frame, 7475.0, scope=ZERO_DTE, ref=date(2026, 5, 26)).empty


def test_build_profile_empty_inputs(session: Session):
    _, frame = load_latest_chain(session, "NOPE")
    assert build_profile(frame, 7475.0).empty
    assert build_profile(None, None).empty


def test_load_latest_chain_effective_oi(session: Session):
    # oi_eff = resting OI + net flow = 4000 + (1200 - 200) = 5000
    _row(session, strike=7400.0, cp="C", oi=4000.0, volm_buy=1200.0, volm_sell=200.0)
    session.commit()
    _, frame = load_latest_chain(session, "SPX")
    assert frame.iloc[0]["oi"] == pytest.approx(5000.0)
