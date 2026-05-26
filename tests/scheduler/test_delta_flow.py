"""Tests for the intraday delta-notional flow collector (record build + upsert)."""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pytest
from sqlalchemy.dialects import postgresql

from trading_intel.greeks.delta_flow import MULTIPLIER
from trading_intel.memory.models import DeltaFlow
from trading_intel.scheduler.jobs.delta_flow import build_record


def _chain() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "opt_kind": ["call", "put", "call", "put"],
            "expiration": ["2026-05-27", "2026-05-27", "2026-06-20", "2026-06-20"],
            "delta": [0.50, -0.40, 0.30, -0.20],
            "volume": [1000, 800, 500, 400],
        }
    )


def test_build_record_values():
    ts = datetime(2026, 5, 26, 10, 5)
    rec = build_record("SPX", ts, _chain(), 100.0)
    assert rec is not None
    assert rec["symbol"] == "SPX" and rec["ts"] == ts and rec["source"] == "convex"
    assert rec["next_expiry"] == date(2026, 5, 27)
    # next-expiry call = delta*vol*spot*multiplier
    assert rec["call_notional_next"] == pytest.approx(0.50 * 1000 * 100.0 * MULTIPLIER)
    assert rec["call_notional_all"] > 0 and rec["put_notional_all"] < 0
    assert abs(rec["call_notional_all"]) > abs(rec["call_notional_next"])  # all >= next


def test_build_record_none_on_bad_input():
    assert build_record("SPX", datetime(2026, 5, 26), pd.DataFrame(), 100.0) is None
    assert build_record("SPX", datetime(2026, 5, 26), _chain(), None) is None


def test_upsert_compiles_for_postgres():
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    rec = {
        "symbol": "SPX", "ts": datetime(2026, 5, 26, 10, 5), "source": "convex",
        "spot": 100.0, "next_expiry": date(2026, 5, 27),
        "call_notional_all": 1.0, "put_notional_all": -2.0,
        "call_notional_next": 0.5, "put_notional_next": -1.0,
    }
    stmt = pg_insert(DeltaFlow).values([rec])
    stmt = stmt.on_conflict_do_update(
        index_elements=["symbol", "ts", "source"],
        set_={"spot": stmt.excluded["spot"]},
    )
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT" in sql and "delta_flow" in sql
