"""tas_crowd_agg (2026-11-13, #97): SPX/SPXW prints summed per minute, whale vs crowd."""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from trading_intel.memory.models import TasCrowdMinute
from trading_intel.scheduler.jobs import tas_crowd_agg

D = date(2026, 11, 13)


@pytest.fixture
def session() -> Session:
    tas_crowd_agg.reset_state()
    engine = create_engine("sqlite://")
    TasCrowdMinute.__table__.create(engine)
    with Session(engine) as s:
        yield s
    tas_crowd_agg.reset_state()


def _tape(rows) -> pd.DataFrame:
    cols = ["time", "symbol", "root", "expiry", "strike", "cp", "price", "size", "side", "delta", "spot"]
    return pd.DataFrame(rows, columns=cols)


def _rows(s: Session) -> list[TasCrowdMinute]:
    return list(s.execute(select(TasCrowdMinute).order_by(TasCrowdMinute.bucket)).scalars())


def test_buckets_sides_and_signed_delta(session):
    df = _tape([
        ["2026-11-13T10:00:05", ".SPXW261113P7500", "SPXW", "2026-11-13", 7500, "P", 1.2, 3, "buy", -0.2, 7550],
        ["2026-11-13T10:00:20", ".SPXW261113P7500", "SPXW", "2026-11-13", 7500, "P", 1.3, 2, "sell", -0.2, 7551],
        ["2026-11-13T10:00:40", ".SPXW261113P7500", "SPXW", "2026-11-13", 7500, "P", 1.25, 50, "buy", -0.2, 7552],
        ["2026-11-13T10:00:41", ".NVDA261120C200", "NVDA", "2026-11-20", 200, "C", 3.0, 5, "buy", 0.5, 190],
    ])
    assert tas_crowd_agg.capture(session, df, trade_date=D, whale_min=20) == 2
    crowd, whale = _rows(session)
    assert (crowd.bucket, crowd.n_prints, crowd.buy_contracts, crowd.sell_contracts) == ("crowd", 2, 3, 2)
    assert crowd.signed_delta == pytest.approx((3 - 2) * -0.2 * 100)
    assert crowd.minute == datetime(2026, 11, 13, 10, 0)
    assert (whale.bucket, whale.buy_contracts) == ("whale", 50)
    assert whale.buy_premium == pytest.approx(1.25 * 50 * 100)
    assert whale.spot == 7552


def test_overlapping_polls_are_not_double_counted(session):
    a = ["2026-11-13T10:00:05", ".SPXW261113C7600", "SPXW", "2026-11-13", 7600, "C", 2.0, 4, "buy", 0.3, 7550]
    b = ["2026-11-13T10:00:35", ".SPXW261113C7600", "SPXW", "2026-11-13", 7600, "C", 2.1, 6, "buy", 0.3, 7551]
    tas_crowd_agg.capture(session, _tape([a]), trade_date=D)
    tas_crowd_agg.capture(session, _tape([a, b]), trade_date=D)   # next poll overlaps print a
    (row,) = _rows(session)
    assert row.n_prints == 2 and row.buy_contracts == 10


def test_restart_skips_prints_already_stored(session):
    a = ["2026-11-13T10:00:05", ".SPXW261113C7600", "SPXW", "2026-11-13", 7600, "C", 2.0, 4, "buy", 0.3, 7550]
    tas_crowd_agg.capture(session, _tape([a]), trade_date=D)
    tas_crowd_agg.reset_state()                                     # daemon restart: memory gone
    b = ["2026-11-13T10:00:50", ".SPXW261113C7600", "SPXW", "2026-11-13", 7600, "C", 2.0, 1, "sell", 0.3, 7550]
    tas_crowd_agg.capture(session, _tape([a, b]), trade_date=D)
    (row,) = _rows(session)
    assert row.n_prints == 2 and row.buy_contracts == 4 and row.sell_contracts == 1


def test_decodes_raw_symbol_when_columns_missing(session):
    df = pd.DataFrame({"time": ["2026-11-13T11:15:00"], "symbol": [".SPXW261113P7450"],
                       "price": [0.5], "size": [1], "side": ["mid"]})
    tas_crowd_agg.capture(session, df, trade_date=D)
    (row,) = _rows(session)
    assert (row.root, row.strike, row.cp, row.other_contracts) == ("SPXW", 7450.0, "P", 1)
