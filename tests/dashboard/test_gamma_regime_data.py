"""Tests for the live gamma-regime loader (SQLite, no network)."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.dashboard.gamma_regime_data import latest_spx_gamma_regime
from trading_intel.memory.models import OiChainEod

_EXPIRY = date(2026, 6, 21)

# (strike, cp, gxoi, iv, oi, delta, dte)
_NEAR = [
    (100.0, "C", 200.0, 0.20, 1000, 0.50, 30),
    (105.0, "C", 150.0, 0.20, 1000, 0.30, 30),
    (110.0, "C", 80.0, 0.20, 1000, 0.10, 30),
    (95.0, "P", 60.0, 0.20, 1000, -0.30, 30),
    (90.0, "P", 40.0, 0.20, 1000, -0.10, 30),
]
# Far-dated row that must be excluded by the DTE filter (else call_wall->130).
_FAR = [(130.0, "C", 9999.0, 0.20, 1000, 0.20, 200)]


def _seed(session: Session, ts: datetime, rows) -> None:
    for strike, cp, gxoi, iv, oi, delta, dte in rows:
        session.add(
            OiChainEod(
                symbol="SPX", ts=ts, expiry=_EXPIRY, strike=strike, cp=cp,
                source="convex_eod", dte=dte, gxoi=gxoi, iv=iv, oi=oi, delta=delta,
            )
        )
    session.commit()


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    OiChainEod.__table__.create(engine)
    with Session(engine) as s:
        yield s


def test_gamma_regime_from_latest_snapshot(session: Session):
    _seed(session, datetime(2026, 5, 23), _NEAR + _FAR)
    gr = latest_spx_gamma_regime(session)
    assert gr is not None
    # net signed gxoi over the NEAR-term rows only: calls(430) - puts(100) = 330.
    assert gr.net_gex == pytest.approx(330.0)
    assert gr.regime == "positive"
    assert gr.call_wall == pytest.approx(100.0)  # far 130 row excluded by DTE filter
    assert "Positive-gamma" in gr.regime_read()


def test_no_data_returns_none(session: Session):
    assert latest_spx_gamma_regime(session) is None
