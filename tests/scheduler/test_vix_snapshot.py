"""Tests for the VIX snapshot job — SQLite + fake FRED/CBOE, no network."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from trading_intel.clients.fred import VIX_SERIES, FredClient
from trading_intel.memory.models import QuoteDaily, VixData
from trading_intel.scheduler.jobs import vix_snapshot


class FakeFred:
    def __init__(self, data):
        self._data = data

    def get_series(self, series_id):
        return self._data.get(series_id)


class FakeCboe:
    def __init__(self, vvix_value, term=None):
        self._vvix = vvix_value
        self._term = term or {}

    def vvix(self):
        return self._vvix

    def term_structure(self):
        return self._term


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    VixData.__table__.create(engine)
    with Session(engine) as s:
        yield s


def _fred(vix_level: float) -> FredClient:
    data = {
        VIX_SERIES: pd.Series([vix_level] * 25),
        "BAMLH0A0HYM2": pd.Series([3.2]),
        "BAMLC0A0CM": pd.Series([1.1]),
    }
    return FredClient(settings=None, fred=FakeFred(data))


def test_run_writes_row_with_zone(session: Session):
    vix_snapshot.run(session, _fred(15.0), FakeCboe(95.0), as_of=date(2026, 5, 22))
    row = session.get(VixData, date(2026, 5, 22))
    assert row is not None
    assert row.vix == pytest.approx(15.0)
    assert row.vvix == pytest.approx(95.0)
    assert row.vega_zone == "low"
    assert row.hy_oas == pytest.approx(3.2)
    assert row.move is None  # MOVE intentionally unset


def test_run_is_idempotent_for_same_date(session: Session):
    vix_snapshot.run(session, _fred(15.0), FakeCboe(95.0), as_of=date(2026, 5, 22))
    vix_snapshot.run(session, _fred(35.0), FakeCboe(110.0), as_of=date(2026, 5, 22))
    assert session.execute(select(func.count()).select_from(VixData)).scalar_one() == 1
    row = session.get(VixData, date(2026, 5, 22))
    assert row.vix == pytest.approx(35.0)  # updated in place
    assert row.vega_zone == "high"


def test_run_persists_term_structure_and_vrp(session: Session):
    # Seed an SPX realized-vol row so VRP = VIX - rv20*100 can be computed.
    QuoteDaily.__table__.create(session.get_bind())
    session.add(
        QuoteDaily(
            symbol="SPX", date=date(2026, 5, 21),
            open=1.0, high=1.0, low=1.0, close=1.0, volume=1, rv20=0.10,
        )
    )
    session.commit()
    term = {"VIX9D": 17.0, "VIX": 16.0, "VIX3M": 18.0, "VIX6M": 19.0}
    vix_snapshot.run(session, _fred(16.0), FakeCboe(95.0, term), as_of=date(2026, 5, 22))
    row = session.get(VixData, date(2026, 5, 22))
    assert row.vix9d == pytest.approx(17.0)
    assert row.vix3m == pytest.approx(18.0)
    assert row.vix6m == pytest.approx(19.0)
    # VRP = VIX(16.0) - rv20(0.10 * 100 = 10.0) = 6.0 vol points
    assert row.vrp == pytest.approx(6.0)


def test_term_structure_failure_degrades_gracefully(session: Session):
    class BoomCboe(FakeCboe):
        def term_structure(self):
            raise RuntimeError("CBOE down")

    vix_snapshot.run(session, _fred(16.0), BoomCboe(95.0), as_of=date(2026, 5, 22))
    row = session.get(VixData, date(2026, 5, 22))
    assert row is not None and row.vix9d is None  # snapshot still written


def test_vvix_sd20_uses_stored_history(session: Session):
    # Seed prior VVIX history so today's run can compute a stdev.
    for i, d in enumerate((date(2026, 5, 20), date(2026, 5, 21))):
        session.add(VixData(date=d, vvix=90.0 + i))
    session.commit()
    vix_snapshot.run(session, _fred(20.0), FakeCboe(100.0), as_of=date(2026, 5, 22))
    row = session.get(VixData, date(2026, 5, 22))
    assert row.vvix_sd20 is not None and row.vvix_sd20 > 0
