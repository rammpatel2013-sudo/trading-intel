"""The widened EOD chain carries quotes + signed flow into oi_chain_eod rows."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from trading_intel.scheduler.jobs import oi_chain_eod as job

TS = datetime(2026, 10, 26, 18, 0)
NEW = ("bid_price", "ask_price", "gammas_buy", "gammas_sell", "deltas_buy", "deltas_sell")


def _frame(**over) -> pd.DataFrame:
    base = {
        "expiration": [pd.Timestamp("2026-10-30")], "strike": [7600.0], "opt_kind": ["call"],
        "oi": [1000], "oi_change": [10], "volume": [50],
        "delta": [0.5], "gamma": [0.001], "iv": [0.14],
        "gxoi": [1.0], "dxoi": [2.0], "vxoi": [3.0],
        "bid_price": [12.3], "ask_price": [12.9],
        "gammas_buy": [4.5], "gammas_sell": [6.1],
        "deltas_buy": [7.2], "deltas_sell": [8.8],
    }
    base.update(over)
    return pd.DataFrame(base)


def test_new_columns_land_on_the_row():
    rows = job._chain_to_records(_frame(), symbol="SPX", ts=TS, window_days=180)
    assert len(rows) == 1
    r = rows[0]
    assert r["bid_price"] == 12.3 and r["ask_price"] == 12.9
    assert r["gammas_buy"] == 4.5 and r["gammas_sell"] == 6.1
    assert r["deltas_buy"] == 7.2 and r["deltas_sell"] == 8.8


def test_signed_flow_is_recoverable_from_the_row():
    """gammas_buy - gammas_sell = net customer-initiated gamma; dealer is the other side."""
    r = job._chain_to_records(_frame(), symbol="SPX", ts=TS, window_days=180)[0]
    assert r["gammas_buy"] - r["gammas_sell"] == pytest.approx(-1.6)  # customers net sellers


def test_missing_new_columns_degrade_to_null_not_crash():
    """A symbol whose pull fell back a tier yields NaN -> NULL, never a dropped row."""
    df = _frame()
    for c in NEW:
        df[c] = float("nan")
    rows = job._chain_to_records(df, symbol="XYZ", ts=TS, window_days=180)
    assert len(rows) == 1
    for c in NEW:
        assert rows[0][c] is None
    assert rows[0]["gxoi"] == 1.0  # the rest of the row is intact


def test_columns_absent_entirely_still_produce_a_row():
    df = _frame().drop(columns=list(NEW))
    rows = job._chain_to_records(df, symbol="XYZ", ts=TS, window_days=180)
    assert len(rows) == 1
    for c in NEW:
        assert rows[0].get(c) is None


def test_new_cols_are_registered_as_floats():
    for c in NEW:
        assert c in job._FLOAT_COLS
    assert set(NEW).isdisjoint(job._INT_COLS)


def test_insert_batch_stays_under_the_postgres_bind_cap():
    """65535 bound params per statement; the row got wider, so re-check."""
    from trading_intel.memory.models import OiChainEod

    n_cols = len(OiChainEod.__table__.columns) - 1  # id is generated
    assert job._INSERT_BATCH * n_cols < 65535, (job._INSERT_BATCH, n_cols)
