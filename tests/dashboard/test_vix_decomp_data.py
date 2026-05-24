"""Tests for the live VIX-decomposition loader (SQLite, no network)."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.dashboard.vix_decomp_data import latest_spx_decomposition
from trading_intel.memory.models import OiChainEod

_EXPIRY = date(2026, 6, 21)

# (strike, cp, iv_decimal, delta)
_PREV_ROWS = [
    (4500, "P", 0.30, -0.10),
    (4800, "P", 0.25, -0.30),
    (5000, "P", 0.22, -0.50),
    (5000, "C", 0.20, 0.50),
    (5200, "C", 0.19, 0.30),
    (5500, "C", 0.18, 0.10),
]
# Day 2: spot fell to ~4800 and the whole surface lifted ~+0.05 (parallel up).
_NOW_ROWS = [
    (4300, "P", 0.36, -0.10),
    (4600, "P", 0.31, -0.30),
    (4800, "P", 0.28, -0.50),
    (4800, "C", 0.27, 0.50),
    (5000, "C", 0.25, 0.30),
    (5300, "C", 0.23, 0.10),
]


def _seed(session: Session, ts: datetime, rows) -> None:
    for strike, cp, iv, delta in rows:
        session.add(
            OiChainEod(
                symbol="SPX", ts=ts, expiry=_EXPIRY, strike=float(strike), cp=cp,
                source="convex_eod", dte=30, iv=iv, delta=delta,
            )
        )
    session.commit()


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    OiChainEod.__table__.create(engine)
    with Session(engine) as s:
        yield s


def test_decomposition_computes_from_two_days(session: Session):
    _seed(session, datetime(2026, 5, 22), _PREV_ROWS)
    _seed(session, datetime(2026, 5, 23), _NOW_ROWS)

    result = latest_spx_decomposition(session)
    assert result.days_available == 2
    d = result.decomposition
    assert d is not None
    # spot slid 5000 -> 4800 up the prev skew (20 -> 25): sticky ~ +5 vol pts.
    assert d.sticky_strike == pytest.approx(5.0, abs=0.1)
    # whole surface +2 at the new ATM (25 -> 27): parallel ~ +2.
    assert d.parallel_shift == pytest.approx(2.0, abs=0.1)
    assert d.dominant == "sticky_strike"
    assert "sticky-strike dominated" in d.regime_read()


def test_one_day_is_not_ready(session: Session):
    _seed(session, datetime(2026, 5, 23), _NOW_ROWS)
    result = latest_spx_decomposition(session)
    assert result.days_available == 1
    assert result.decomposition is None
