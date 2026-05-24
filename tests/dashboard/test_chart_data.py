"""Tests for the charting data loader (SQLite, no network)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.dashboard.chart_data import chart_frame, list_chart_symbols
from trading_intel.memory.models import GreeksSnapshot, QuoteDaily


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    QuoteDaily.__table__.create(engine)
    GreeksSnapshot.__table__.create(engine)
    with Session(engine) as s:
        yield s


def test_list_and_chart_frame(session: Session):
    for i in range(20):
        session.add(QuoteDaily(
            symbol="SPX", date=date(2026, 5, 1) + timedelta(days=i),
            open=1.0, high=1.0, low=1.0, close=100.0 + i, volume=1, rv20=0.10,
        ))
    session.add(GreeksSnapshot(
        symbol="SPX", ts=datetime(2026, 5, 19), gex_total=1.5e9, dex_total=2e8,
        atm_iv=0.20, source="convex",
    ))
    session.commit()

    assert "SPX" in list_chart_symbols(session)
    df = chart_frame(session, "SPX")
    assert not df.empty
    assert {"close", "rsi", "gex", "dex", "atm_iv", "iv_hv"}.issubset(df.columns)
    row = df[df["atm_iv"].notna()].iloc[0]
    assert row["iv_hv"] == pytest.approx(10.0)  # (0.20 - 0.10) * 100
    assert df["rsi"].iloc[-1] == pytest.approx(100.0)  # strictly rising close


def test_empty_symbol(session: Session):
    assert chart_frame(session, "NOPE").empty
