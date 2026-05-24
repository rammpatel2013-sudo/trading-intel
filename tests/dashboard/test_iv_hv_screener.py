"""Tests for the IV-HV screener (pure rank in sandbox; loader on real DB)."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.dashboard.iv_hv_screener import iv_hv_table, rank_iv_hv
from trading_intel.memory.models import OiChainEod, QuoteDaily


def test_rank_orders_by_spread_and_labels():
    rows = [
        {"symbol": "A", "iv30": 0.30, "hv30": 0.20, "iv60": 0.28, "hv60": 0.20},
        {"symbol": "B", "iv30": 0.18, "hv30": 0.20, "iv60": 0.19, "hv60": 0.20},
        {"symbol": "C", "iv30": 0.15, "hv30": 0.25, "iv60": 0.16, "hv60": 0.24},
    ]
    out = rank_iv_hv(rows)
    assert list(out["symbol"]) == ["A", "B", "C"]  # by spread30 desc
    assert out.iloc[0]["spread30"] == pytest.approx(0.10)
    assert list(out["label"]) == ["rich (sell-vol)", "fair", "cheap (buy-vol)"]


def test_rank_empty():
    assert rank_iv_hv([]).empty


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    OiChainEod.__table__.create(engine)
    QuoteDaily.__table__.create(engine)
    with Session(engine) as s:
        yield s


def test_iv_hv_table_from_db(session: Session):
    ts = datetime(2026, 5, 23)
    exp = date(2026, 6, 21)  # ~30 DTE from the snapshot
    for K in (4800, 4900, 5000, 5100, 5200):
        m = K / 5000.0
        cd = max(0.05, min(0.95, 0.5 - (m - 1) * 2.5))
        for cp, d in (("C", cd), ("P", cd - 1.0)):
            session.add(OiChainEod(
                symbol="SPX", ts=ts, expiry=exp, strike=float(K), cp=cp,
                source="convex_eod", iv=0.20, delta=d, dte=30,
            ))
    session.add(QuoteDaily(
        symbol="SPX", date=date(2026, 5, 23), open=1.0, high=1.0, low=1.0,
        close=1.0, volume=1, rv20=0.12, rv60=0.14,
    ))
    session.commit()

    out = iv_hv_table(session, ["SPX"])
    assert not out.empty
    row = out.iloc[0]
    assert row["symbol"] == "SPX"
    assert row["iv30"] == pytest.approx(0.20, abs=0.01)
    assert row["hv30"] == pytest.approx(0.12)
    assert row["spread30"] == pytest.approx(0.08, abs=0.01)  # rich
    assert row["label"] == "rich (sell-vol)"
