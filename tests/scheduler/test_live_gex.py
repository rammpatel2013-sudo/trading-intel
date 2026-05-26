"""Tests for the live-GEX collector (record build + upsert) and its prune."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from trading_intel.memory.models import LiveGex
from trading_intel.scheduler.jobs import prune_live_gex
from trading_intel.scheduler.jobs.live_gex import build_records


def _chain() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "opt_kind": ["call", "call", "put", "call"],
            "strike": [100, 105, 100, 120],
            "delta": [0.50, 0.32, -0.50, 0.10],  # 0.10 is outside the band -> dropped
            "gamma": [0.01, 0.008, 0.011, 0.002],
            "iv": [0.20, 0.21, 0.22, 0.25],
            "gxoi": [1e6, 5e5, 8e5, 1e5],
            "dxoi": [2e6, 1e6, -1.5e6, 3e5],
        }
    )


def test_build_records_delta_band_and_fields():
    recs = build_records(
        _chain(), symbol="SPX", ts=datetime(2026, 5, 26, 10, 0), spot=100.0, lo=0.30, hi=0.70
    )
    assert len(recs) == 3  # the 0.10-delta strike is filtered out
    assert 120.0 not in [r["strike"] for r in recs]
    r0 = next(r for r in recs if r["strike"] == 100.0 and r["cp"] == "C")
    assert r0["symbol"] == "SPX" and r0["spot"] == 100.0 and r0["gxoi"] == 1e6


def test_build_records_empty():
    out = build_records(
        pd.DataFrame(), symbol="X", ts=datetime(2026, 5, 26), spot=100.0, lo=0.3, hi=0.7
    )
    assert out == []


def test_upsert_compiles_for_postgres():
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    rec = {
        "symbol": "SPX", "ts": datetime(2026, 5, 26, 10, 0), "source": "convex",
        "strike": 100.0, "cp": "C", "spot": 100.0, "delta": 0.5, "gamma": 0.01,
        "iv": 0.2, "gxoi": 1e6, "dxoi": 2e6,
    }
    stmt = pg_insert(LiveGex).values([rec])
    stmt = stmt.on_conflict_do_update(
        index_elements=["symbol", "ts", "strike", "cp"],
        set_={"gxoi": stmt.excluded["gxoi"]},
    )
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT" in sql and "live_gex" in sql


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    LiveGex.__table__.create(engine)
    with Session(engine) as s:
        yield s


def test_prune_live_gex_deletes_old(session: Session):
    now = datetime(2026, 5, 26, 16, 0)
    session.add_all([
        LiveGex(symbol="SPX", ts=now - timedelta(hours=30), strike=100.0, cp="C", source="convex"),
        LiveGex(symbol="SPX", ts=now - timedelta(hours=2), strike=100.0, cp="P", source="convex"),
    ])
    session.commit()
    deleted = prune_live_gex.run(session, retention_hours=24, now=now)
    assert deleted == 1
    remaining = session.execute(select(LiveGex)).scalars().all()
    assert len(remaining) == 1 and remaining[0].cp == "P"
