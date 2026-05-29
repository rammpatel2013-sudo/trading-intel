"""Tests for the EOD wide-chain collector mapping (no DB, no network)."""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from trading_intel.scheduler.jobs.oi_chain_eod import _chain_to_records


def _chain() -> pd.DataFrame:
    return pd.DataFrame(
        [
            # near-dated: kept
            {"expiration": pd.Timestamp("2026-05-26"), "strike": 7500.0, "opt_kind": "call",
             "oi": 1200, "oi_change": 150, "volume": 800, "gxoi": 1.0e6, "dxoi": 5.0e5,
             "vxoi": 2.0e5, "gamma": 0.01, "delta": 0.5, "iv": 0.18},
            {"expiration": pd.Timestamp("2026-05-26"), "strike": 7400.0, "opt_kind": "put",
             "oi": 900, "oi_change": -75, "volume": 600, "gxoi": 4.0e5, "dxoi": -3.0e5,
             "vxoi": 1.0e5, "gamma": 0.008, "delta": -0.4, "iv": 0.2},
            # far-dated (~200 DTE): dropped by the 180d window
            {"expiration": pd.Timestamp("2026-12-08"), "strike": 8000.0, "opt_kind": "call",
             "oi": 50, "oi_change": 5, "volume": 10, "gxoi": 9.0e4, "dxoi": 1.0e4,
             "vxoi": 1.0e4, "gamma": 0.002, "delta": 0.3, "iv": 0.25},
        ]
    )


def test_window_filter_and_field_mapping():
    ts = datetime(2026, 5, 22, 0, 0)
    recs = _chain_to_records(_chain(), symbol="SPX", ts=ts, window_days=180)

    strikes = {r["strike"] for r in recs}
    assert strikes == {7500.0, 7400.0}  # far-dated 8000 dropped

    call = next(r for r in recs if r["strike"] == 7500.0)
    assert call["cp"] == "C"
    assert call["oi"] == 1200 and call["oi_change"] == 150 and call["volume"] == 800
    assert call["dte"] == 4  # 2026-05-26 minus 2026-05-22
    assert call["symbol"] == "SPX" and call["source"] == "convex_eod"


def test_empty_and_missing_columns():
    ts = datetime(2026, 5, 22)
    assert _chain_to_records(pd.DataFrame(), symbol="SPX", ts=ts, window_days=180) == []
    bad = pd.DataFrame([{"strike": 1.0}])  # missing expiration/opt_kind
    assert _chain_to_records(bad, symbol="SPX", ts=ts, window_days=180) == []


def test_nan_oi_change_becomes_none():
    chain = _chain().head(1).copy()
    chain["oi_change"] = float("nan")
    recs = _chain_to_records(chain, symbol="SPX", ts=datetime(2026, 5, 22), window_days=180)
    assert recs[0]["oi_change"] is None


def test_run_batches_inserts(monkeypatch):
    """run() must chunk the multi-row INSERT (Postgres caps params at 65535)."""
    from sqlalchemy import create_engine, func, select
    from sqlalchemy.orm import Session

    from trading_intel.memory.models import OiChainEod
    from trading_intel.scheduler.jobs import oi_chain_eod as job

    engine = create_engine("sqlite://")
    OiChainEod.__table__.create(engine)

    n_rows = 120
    # Forward-dated expiry so _chain_to_records' dte >= 0 window filter keeps
    # every row. This test is about batching, not date filtering.
    from trading_intel.timeutils import eastern_now

    future_exp = pd.Timestamp(eastern_now().date()) + pd.Timedelta(days=30)
    chain = pd.DataFrame(
        [
            {"expiration": future_exp, "strike": 5000.0 + i,
             "opt_kind": "call" if i % 2 else "put", "oi": 10, "oi_change": 1,
             "volume": 5, "gxoi": 1.0, "dxoi": 1.0, "vxoi": 1.0, "gamma": 0.0,
             "delta": 0.0, "iv": 0.1}
            for i in range(n_rows)
        ]
    )

    class _Source:
        def chain_long(self, symbol: str) -> pd.DataFrame:
            return chain

    monkeypatch.setattr(job, "effective_symbols", lambda session, settings: ["BIG"])
    monkeypatch.setattr(job, "_INSERT_BATCH", 50)  # 120 rows -> 3 batches

    with Session(engine) as session:
        orig = session.execute
        calls = {"n": 0}

        def _counting(*a, **k):
            calls["n"] += 1
            return orig(*a, **k)

        monkeypatch.setattr(session, "execute", _counting)
        job.run(session, _Source(), settings=object())
        monkeypatch.setattr(session, "execute", orig)
        total = session.scalar(select(func.count()).select_from(OiChainEod))

    assert total == n_rows           # every row written across batches
    assert calls["n"] == 3           # ceil(120 / 50) INSERT statements
