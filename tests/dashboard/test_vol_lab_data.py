"""Tests for the vol-lab loaders (SQLite, no network)."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.dashboard.vol_lab_data import latest_spx_chain, prev_curr_spx_chains
from trading_intel.memory.models import OiChainEod

_EXPIRY = date(2026, 6, 21)
# call delta per strike so the ~0.50-delta strike (the spot proxy) is 5000.
_CALL_DELTA = {4800: 0.62, 4900: 0.56, 5000: 0.50, 5100: 0.44, 5200: 0.38}


def _seed(session: Session, ts: datetime) -> None:
    for strike, cdelta in _CALL_DELTA.items():
        session.add(OiChainEod(
            symbol="SPX", ts=ts, expiry=_EXPIRY, strike=float(strike), cp="C",
            source="convex_eod", iv=0.20, delta=cdelta, dte=30,
        ))
        session.add(OiChainEod(
            symbol="SPX", ts=ts, expiry=_EXPIRY, strike=float(strike), cp="P",
            source="convex_eod", iv=0.20, delta=cdelta - 1.0, dte=30,
        ))
    session.commit()


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    OiChainEod.__table__.create(engine)
    with Session(engine) as s:
        yield s


def test_latest_chain_is_mapped(session: Session):
    _seed(session, datetime(2026, 5, 23))
    res = latest_spx_chain(session)
    assert res is not None
    df, spot, ts = res
    assert "opt_kind" in df.columns and "expiration" in df.columns
    assert spot == pytest.approx(5000.0)  # 0.50-delta call strike
    assert prev_curr_spx_chains(session) is None  # only one day


def test_prev_curr_needs_two_days(session: Session):
    _seed(session, datetime(2026, 5, 22))
    _seed(session, datetime(2026, 5, 23))
    pair = prev_curr_spx_chains(session)
    assert pair is not None and len(pair) == 2
