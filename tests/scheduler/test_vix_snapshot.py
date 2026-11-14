"""Tests for the VIX snapshot job — SQLite + fake FRED/CBOE, no network.

2026-10-29: VIX now comes from Cboe's same-day close; FRED values are filed
under their own observation dates (FRED posts a day late).
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from trading_intel.clients.fred import VIX_SERIES, FredClient
from trading_intel.memory.models import QuoteDaily, VixData
from trading_intel.scheduler.jobs import vix_snapshot

FRI = date(2026, 5, 22)
THU = date(2026, 5, 21)


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


def _dated(values: dict[date, float]) -> pd.Series:
    return pd.Series(list(values.values()), index=pd.DatetimeIndex(list(values)))


def _fred(vix_by_day=None, hy=None, ig=None) -> FredClient:
    """FRED as it really is at 16:45: the newest observation is YESTERDAY's."""
    data = {
        VIX_SERIES: _dated(vix_by_day or {THU: 14.0}),
        "BAMLH0A0HYM2": _dated(hy or {THU: 3.2}),
        "BAMLC0A0CM": _dated(ig or {THU: 1.1}),
    }
    return FredClient(settings=None, fred=FakeFred(data))


def test_vix_is_cboe_same_day_not_freds_previous_close(session: Session):
    vix_snapshot.run(session, _fred(), FakeCboe(95.0, {"VIX": 15.0}), as_of=FRI)
    row = session.get(VixData, FRI)
    assert row.vix == pytest.approx(15.0)       # Cboe's Friday close, not FRED's Thursday 14.0
    assert row.vvix == pytest.approx(95.0)
    assert row.vega_zone == "low"
    assert row.hy_oas is None                    # Friday's spread isn't on FRED yet
    assert row.move is None                      # MOVE intentionally unset


def test_credit_is_filed_under_its_own_date(session: Session):
    session.add(VixData(date=THU, vix=14.0))
    session.commit()
    vix_snapshot.run(session, _fred(hy={THU: 3.2}, ig={THU: 1.1}),
                     FakeCboe(95.0, {"VIX": 15.0}), as_of=FRI)
    assert session.get(VixData, THU).hy_oas == pytest.approx(3.2)
    assert session.get(VixData, THU).ig_oas == pytest.approx(1.1)
    assert session.get(VixData, FRI).hy_oas is None


def test_fred_vix_fallback_only_for_the_same_date(session: Session):
    # Cboe down: FRED has no Friday value yet -> vix stays empty, not Thursday's.
    vix_snapshot.run(session, _fred(), FakeCboe(95.0, {}), as_of=FRI)
    assert session.get(VixData, FRI).vix is None
    # FRED already carries Friday (a late re-run) -> it is used.
    vix_snapshot.run(session, _fred(vix_by_day={THU: 14.0, FRI: 15.5}), FakeCboe(95.0, {}), as_of=FRI)
    assert session.get(VixData, FRI).vix == pytest.approx(15.5)


def test_run_is_idempotent_for_same_date(session: Session):
    vix_snapshot.run(session, _fred(), FakeCboe(95.0, {"VIX": 15.0}), as_of=FRI)
    vix_snapshot.run(session, _fred(), FakeCboe(110.0, {"VIX": 35.0}), as_of=FRI)
    assert session.execute(select(func.count()).select_from(VixData)).scalar_one() == 1
    row = session.get(VixData, FRI)
    assert row.vix == pytest.approx(35.0)  # updated in place
    assert row.vega_zone == "high"


def test_run_persists_term_structure_and_vrp(session: Session):
    # Seed an SPX realized-vol row so VRP = VIX - rv20*100 can be computed.
    QuoteDaily.__table__.create(session.get_bind())
    session.add(
        QuoteDaily(
            symbol="SPX", date=THU,
            open=1.0, high=1.0, low=1.0, close=1.0, volume=1, rv20=0.10,
        )
    )
    session.commit()
    term = {"VIX9D": 17.0, "VIX": 16.0, "VIX3M": 18.0, "VIX6M": 19.0}
    vix_snapshot.run(session, _fred(), FakeCboe(95.0, term), as_of=FRI)
    row = session.get(VixData, FRI)
    assert row.vix9d == pytest.approx(17.0)
    assert row.vix3m == pytest.approx(18.0)
    assert row.vix6m == pytest.approx(19.0)
    # VRP = VIX(16.0) - rv20(0.10 * 100 = 10.0) = 6.0 vol points
    assert row.vrp == pytest.approx(6.0)


def test_term_structure_failure_degrades_gracefully(session: Session):
    class BoomCboe(FakeCboe):
        def term_structure(self):
            raise RuntimeError("CBOE down")

    vix_snapshot.run(session, _fred(), BoomCboe(95.0), as_of=FRI)
    row = session.get(VixData, FRI)
    assert row is not None and row.vix9d is None  # snapshot still written


def test_sd20_uses_stored_history(session: Session):
    for i, d in enumerate((date(2026, 5, 20), THU)):
        session.add(VixData(date=d, vvix=90.0 + i, vix=14.0 + i))
    session.commit()
    vix_snapshot.run(session, _fred(), FakeCboe(100.0, {"VIX": 20.0}), as_of=FRI)
    row = session.get(VixData, FRI)
    assert row.vvix_sd20 is not None and row.vvix_sd20 > 0
    assert row.vix_sd20 == pytest.approx(pd.Series([20.0, 15.0, 14.0]).std(ddof=0))


def test_skips_non_sessions(session: Session):
    vix_snapshot.run(session, _fred(), FakeCboe(95.0, {"VIX": 15.0}), as_of=date(2026, 5, 23))  # Sat
    assert session.execute(select(func.count()).select_from(VixData)).scalar_one() == 0


# ---------------------------------------------------------------- 2026-11-13 data check D7
TUE = date(2026, 5, 26)  # first session after Fri 5/22 (Mon 5/25 = Memorial Day)


def test_stale_copy_of_prior_term_structure_is_not_filed(session: Session):
    term = {"VIX": 15.0, "VIX9D": 14.1, "VIX3M": 17.2, "VIX6M": 18.3}
    vix_snapshot.run(session, _fred(), FakeCboe(95.0, term), as_of=FRI)
    # next session: VIX moved, everything else is an exact copy of Friday -> not filed
    copy = dict(term, VIX=16.4)
    vix_snapshot.run(session, _fred(), FakeCboe(95.0, copy), as_of=TUE)
    row = session.get(VixData, TUE)
    assert row.vix == 16.4
    assert row.vvix is None and row.vix9d is None and row.vix3m is None and row.vix6m is None


def test_real_change_is_filed(session: Session):
    term = {"VIX": 15.0, "VIX9D": 14.1, "VIX3M": 17.2, "VIX6M": 18.3}
    vix_snapshot.run(session, _fred(), FakeCboe(95.0, term), as_of=FRI)
    vix_snapshot.run(session, _fred(), FakeCboe(96.0, dict(term, VIX=16.4, VIX9D=15.0)), as_of=TUE)
    row = session.get(VixData, TUE)
    assert row.vvix == 96.0 and row.vix9d == 15.0


def test_move_close_is_filed_and_failure_is_harmless(session: Session):
    vix_snapshot.run(session, _fred(), FakeCboe(95.0, {"VIX": 15.0}), as_of=FRI,
                     move_close=lambda d: 98.7)
    assert session.get(VixData, FRI).move == 98.7

    def boom(d):
        raise RuntimeError("fmp down")

    vix_snapshot.run(session, _fred(), FakeCboe(95.0, {"VIX": 15.0}), as_of=TUE, move_close=boom)
    assert session.get(VixData, TUE).move is None
