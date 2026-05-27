"""Tests for the live-GEX collector (record build + upsert) and its prune."""

from __future__ import annotations

from datetime import date, datetime, timedelta

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
            "oi": [5000, 3000, 4000, 1000],
            "vanna": [0.03, 0.02, -0.02, 0.01],
            "charm": [-0.01, -0.008, 0.015, 0.002],
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
    assert r0["oi"] == 5000 and r0["vanna"] == 0.03 and r0["charm"] == -0.01


def test_build_records_collapses_same_strike_same_expiry():
    # duplicate (strike, cp, expiry) rows merge to ONE (the live_gex grain) or the
    # upsert hits CardinalityViolation. gxoi/dxoi/oi sum; greeks are OI-weighted so
    # that greek*oi still equals the total. Same expiration on both rows here.
    exp = datetime(2026, 5, 29)
    chain = pd.DataFrame(
        {
            "opt_kind": ["call", "call"],
            "strike": [100, 100],
            "expiration": [exp, exp],
            "delta": [0.50, 0.45],
            "gamma": [0.01, 0.02],
            "iv": [0.20, 0.22],
            "gxoi": [1e6, 5e5],
            "dxoi": [2e6, 1e6],
            "oi": [4000, 1000],
            "vanna": [0.03, 0.08],
            "charm": [-0.01, -0.02],
        }
    )
    recs = build_records(
        chain, symbol="SPX", ts=datetime(2026, 5, 26, 10, 0), spot=100.0, lo=0.30, hi=0.70
    )
    assert len(recs) == 1
    r = recs[0]
    assert r["gxoi"] == 1.5e6 and r["dxoi"] == 3e6 and r["oi"] == 5000
    assert r["expiry"] == exp.date()
    # OI-weighted vanna = (0.03*4000 + 0.08*1000)/5000 = 0.04 -> vanna*oi == total
    assert r["vanna"] == pytest.approx(0.04)
    assert r["vanna"] * r["oi"] == pytest.approx(0.03 * 4000 + 0.08 * 1000)
    assert r["charm"] * r["oi"] == pytest.approx(-0.01 * 4000 + -0.02 * 1000)


def test_build_records_keeps_expiries_separate():
    # same strike+cp but two DIFFERENT expiries -> two rows (per-expiry decomposition)
    chain = pd.DataFrame(
        {
            "opt_kind": ["call", "call"],
            "strike": [100, 100],
            "expiration": [datetime(2026, 5, 26), datetime(2026, 7, 17)],
            "delta": [0.50, 0.45],
            "gamma": [0.01, 0.02],
            "iv": [0.20, 0.22],
            "gxoi": [1e6, 5e5],
            "dxoi": [2e6, 1e6],
            "oi": [4000, 1000],
            "vanna": [0.03, 0.08],
            "charm": [-0.01, -0.02],
        }
    )
    recs = build_records(
        chain, symbol="SPX", ts=datetime(2026, 5, 26, 10, 0), spot=100.0, lo=0.30, hi=0.70
    )
    assert len(recs) == 2
    expiries = sorted(r["expiry"] for r in recs)
    assert expiries == [date(2026, 5, 26), date(2026, 7, 17)]
    by_exp = {r["expiry"]: r for r in recs}
    assert by_exp[date(2026, 5, 26)]["gxoi"] == 1e6
    assert by_exp[date(2026, 7, 17)]["gxoi"] == 5e5


def test_build_records_empty():
    out = build_records(
        pd.DataFrame(), symbol="X", ts=datetime(2026, 5, 26), spot=100.0, lo=0.3, hi=0.7
    )
    assert out == []


def test_upsert_compiles_for_postgres():
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    rec = {
        "symbol": "SPX", "ts": datetime(2026, 5, 26, 10, 0), "source": "convex",
        "strike": 100.0, "cp": "C", "expiry": date(2026, 5, 29), "spot": 100.0,
        "delta": 0.5, "gamma": 0.01, "iv": 0.2, "gxoi": 1e6, "dxoi": 2e6,
    }
    stmt = pg_insert(LiveGex).values([rec])
    stmt = stmt.on_conflict_do_update(
        index_elements=["symbol", "ts", "strike", "cp", "expiry"],
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
