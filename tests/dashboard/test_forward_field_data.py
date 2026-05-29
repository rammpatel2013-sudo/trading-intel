"""Tests for the forward-field data layer (SQLite)."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.dashboard.forward_field_data import build_forward_fields
from trading_intel.memory.models import LiveGex


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    LiveGex.__table__.create(engine)
    with Session(engine) as s:
        yield s


def _row(session: Session, **kw) -> None:
    base = dict(
        symbol="SPX", ts=datetime(2026, 5, 26, 11, 0), strike=7400.0, cp="C",
        expiry=date(2026, 5, 26), source="convex", spot=7400.0, iv=0.18, oi=5000.0,
    )
    base.update(kw)
    session.add(LiveGex(**base))


def test_build_forward_fields_gamma_and_charm(session: Session):
    for k in (7350.0, 7400.0, 7450.0):
        _row(session, strike=k, cp="C")
    session.commit()
    now = datetime(2026, 5, 26, 11, 0)
    ts, anchor, grid, gamma, charm = build_forward_fields(session, "SPX", spot=7400.0, now=now)
    assert anchor == 7400.0
    assert grid[-1] == datetime(2026, 5, 26, 16, 0)
    assert not gamma.empty and not charm.empty
    assert list(gamma.columns) == grid
    # ATM gamma sharpens toward the close vs the first projected step
    assert abs(gamma.loc[7400.0].iloc[-1]) > abs(gamma.loc[7400.0].iloc[0])


def test_build_forward_fields_no_data(session: Session):
    now = datetime(2026, 5, 26, 11, 0)
    ts, anchor, grid, gamma, charm = build_forward_fields(session, "NOPE", now=now)
    assert gamma.empty and charm.empty


def test_build_forward_fields_0dte_scope_excludes_far(session: Session):
    _row(session, strike=7400.0, cp="C", expiry=date(2026, 5, 26))   # 0DTE
    _row(session, strike=7400.0, cp="C", expiry=date(2026, 7, 17))   # far
    session.commit()
    now = datetime(2026, 5, 26, 11, 0)
    _, _, _, gamma, _ = build_forward_fields(session, "SPX", spot=7400.0, now=now, scope_0dte=True)
    # only the 0DTE strike contributes -> single strike row
    assert list(gamma.index) == [7400.0]
