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
    # run() skips weekends/holidays (is_trading_session gate) - keep this test date-proof (2026-11-14)
    monkeypatch.setattr(job, "is_trading_session", lambda d: True)

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
    assert calls["n"] == 4           # 1 DELETE of that day's 08:35 pull + ceil(120 / 50) INSERTs


def _full_chain() -> pd.DataFrame:
    from trading_intel.timeutils import eastern_now

    near = pd.Timestamp(eastern_now().date()) + pd.Timedelta(days=3)
    leaps = pd.Timestamp(eastern_now().date()) + pd.Timedelta(days=1800)
    return pd.DataFrame([
        {"symbol": ".SPXW261123P6200", "expiration": near, "strike": 6200.0, "opt_kind": "put", "oi": 500,
         "volume": 40, "volm_buy": 30.0, "volm_sell": 10.0, "value_buy": 6000.0, "value_sell": 1900.0},
        {"symbol": ".SPX311219C9000", "expiration": leaps, "strike": 9000.0, "opt_kind": "call", "oi": 50,
         "volume": 0, "volm_buy": 0.0, "volm_sell": 0.0, "value_buy": 0.0, "value_sell": 0.0},
    ])


def test_full_chain_records_signed_and_am_tag():
    from trading_intel.timeutils import eastern_now

    ts = eastern_now().replace(hour=0, minute=0, second=0, microsecond=0)
    recs = _chain_to_records(_full_chain(), symbol="SPX", ts=ts, window_days=4000, source="cvx_full", signed=True)
    assert len(recs) == 2                                   # the 5-year LEAPS is kept
    put = next(r for r in recs if r["cp"] == "P")
    assert put["source"] == "cvx_full"
    assert (put["vol_buy"], put["vol_sell"], put["val_buy"], put["val_sell"]) == (30.0, 10.0, 6000.0, 1900.0)
    call = next(r for r in recs if r["cp"] == "C")
    assert call["source"] == "cvx_full_set"                 # .SPX root = AM-settled
    plain = _chain_to_records(_full_chain(), symbol="SPX", ts=ts, window_days=4000)
    assert "vol_buy" not in plain[0]                        # the watchlist path is unchanged


def test_run_writes_spx_full_first_and_full_only(monkeypatch):
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from trading_intel.memory.models import OiChainEod
    from trading_intel.scheduler.jobs import oi_chain_eod as job

    engine = create_engine("sqlite://")
    OiChainEod.__table__.create(engine)
    order = []

    class _Source:
        def full_chain(self, symbol):
            order.append("full:" + symbol)
            return _full_chain()

        def chain_long(self, symbol):
            order.append("long:" + symbol)
            return _full_chain().drop(columns=["volm_buy", "volm_sell", "value_buy", "value_sell"])

    monkeypatch.setattr(job, "effective_symbols", lambda session, settings: ["AAA"])
    monkeypatch.setattr(job, "is_trading_session", lambda d: True)
    with Session(engine) as session:
        job.run(session, _Source(), settings=object(), full_only=True)
        assert order == ["full:SPX"]
        job.run(session, _Source(), settings=object())
        srcs = sorted(set(session.scalars(select(OiChainEod.source)).all()))
        signed = session.scalars(select(OiChainEod.vol_buy).where(OiChainEod.source == "cvx_full")).all()
    assert order == ["full:SPX", "full:SPX", "long:AAA"]
    assert srcs == ["convex_eod", "cvx_full", "cvx_full_set"]   # AM tag is SPX-only
    assert signed == [30.0]                                 # re-run: ON CONFLICT DO NOTHING, no duplicate


def test_prune_keeps_full_chain_rows():
    from datetime import timedelta

    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from trading_intel.memory.models import OiChainEod
    from trading_intel.scheduler.jobs import prune_oi_chain

    engine = create_engine("sqlite://")
    OiChainEod.__table__.create(engine)
    old = datetime(2025, 1, 2)
    with Session(engine) as s:
        for src in ("convex_eod", "cvx_full", "cvx_full_set"):
            s.add(OiChainEod(symbol="SPX", ts=old, expiry=old.date() + timedelta(days=30), strike=1.0, cp="C",
                             source=src))
        s.commit()

        class _S:
            OI_CHAIN_RETENTION_DAYS = 365

        prune_oi_chain.run(s, settings=_S(), now=datetime(2026, 11, 21))
        left = sorted(s.scalars(select(OiChainEod.source)).all())
    assert left == ["cvx_full", "cvx_full_set"]
