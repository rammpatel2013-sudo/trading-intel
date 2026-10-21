"""Tests for the AM index-walls snapshot job and the greeks index-union fix."""

from __future__ import annotations

import pandas as pd
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from trading_intel.memory.models import OiChainEod
from trading_intel.timeutils import eastern_now


def test_index_walls_am_writes_index_chain(monkeypatch) -> None:
    """The AM job snapshots the index chain into oi_chain_eod under source=convex_am."""
    from trading_intel.scheduler.jobs import index_walls_am as job

    engine = create_engine("sqlite://")
    OiChainEod.__table__.create(engine)

    future = pd.Timestamp(eastern_now().date()) + pd.Timedelta(days=30)
    chain = pd.DataFrame(
        [
            {"expiration": future, "strike": 7500.0, "opt_kind": "call", "oi": 100,
             "oi_change": 1, "volume": 5, "gxoi": 1.0e6, "dxoi": 1.0, "vxoi": 1.0,
             "gamma": 0.0, "delta": 0.5, "iv": 0.18},
            {"expiration": future, "strike": 7400.0, "opt_kind": "put", "oi": 90,
             "oi_change": -1, "volume": 4, "gxoi": 9.0e5, "dxoi": -1.0, "vxoi": 1.0,
             "gamma": 0.0, "delta": -0.4, "iv": 0.2},
        ]
    )

    class _Source:
        def chain_long(self, symbol: str, **kw) -> pd.DataFrame:
            calls.append(kw)
            return chain

    calls: list[dict] = []

    class _Settings:
        index_roots = ["SPX"]

    with Session(engine) as session:
        job.run(session, _Source(), settings=_Settings(), symbols=["SPX"])
        rows = session.scalars(select(OiChainEod).where(OiChainEod.symbol == "SPX")).all()

    assert len(rows) == 2
    assert {r.strike for r in rows} == {7500.0, 7400.0}
    assert all(r.source == "convex_am" for r in rows)
    assert calls == [{}]


def test_index_walls_am_keeps_same_day_expiry() -> None:
    """Today's expiry (the 0DTE book) is stored with dte 0, not dropped."""
    from trading_intel.scheduler.jobs import index_walls_am as job

    engine = create_engine("sqlite://")
    OiChainEod.__table__.create(engine)
    today = pd.Timestamp(eastern_now().date())
    chain = pd.DataFrame(
        [
            {"expiration": today, "strike": 7750.0, "opt_kind": "call", "oi": 5000,
             "oi_change": 10, "volume": 0, "gxoi": 5.0e8, "dxoi": 1.0, "vxoi": 1.0,
             "gamma": 0.01, "delta": 0.5, "iv": 0.12},
            {"expiration": today + pd.Timedelta(days=1), "strike": 7750.0, "opt_kind": "call",
             "oi": 3000, "oi_change": 5, "volume": 0, "gxoi": 1.0e8, "dxoi": 1.0, "vxoi": 1.0,
             "gamma": 0.005, "delta": 0.5, "iv": 0.13},
        ]
    )

    class _Source:
        def chain_long(self, symbol: str, **kw) -> pd.DataFrame:
            return chain

    class _Settings:
        index_roots = ["SPX"]

    with Session(engine) as session:
        job.run(session, _Source(), settings=_Settings(), symbols=["SPX"])
        dtes = sorted(r.dte for r in session.scalars(select(OiChainEod)).all())
    assert dtes == [0, 1]


def test_greeks_snapshot_unions_index_roots(monkeypatch) -> None:
    """greeks_snapshot must always query the index roots, not just the watchlist."""
    from trading_intel.scheduler.jobs import greeks_snapshot as job

    called: list[str] = []

    class _Source:
        def exposures(self, symbol: str) -> dict:
            called.append(symbol)
            return {}  # empty -> loop skips the insert; we only assert the call set

    class _Settings:
        index_roots = ["SPX", "SPY", "QQQ"]

    class _Session:
        bind = None

        def execute(self, *a, **k) -> None:
            return None

        def commit(self) -> None:
            return None

    monkeypatch.setattr(job, "effective_symbols", lambda session, settings: ["AAPL"])
    job.run(_Session(), _Source(), settings=_Settings())

    assert {"SPX", "SPY", "QQQ"}.issubset(set(called))  # indices always collected
    assert "AAPL" in called  # watchlist preserved


def test_index_walls_am_adds_stocks_with_a_0dte_today() -> None:
    """Names whose last EOD chain lists today's expiry get a morning pull; others don't."""
    from datetime import datetime, timedelta

    from trading_intel.scheduler.jobs import index_walls_am as job

    engine = create_engine("sqlite://")
    OiChainEod.__table__.create(engine)
    today = eastern_now().date()
    last_night = datetime.combine(today - timedelta(days=1), datetime.min.time())
    with Session(engine) as session:
        for sym, exp, oi in (("AAPL", today, 5000), ("NVDA", today, 9000),
                             ("KR", today + timedelta(days=2), 3000), ("SPY", today, 99999)):
            session.add(OiChainEod(symbol=sym, ts=last_night, expiry=exp, strike=100.0, cp="C",
                                   dte=1, oi=oi, source="convex_eod"))
        session.commit()

        pulled: list[str] = []
        future = pd.Timestamp(today) + pd.Timedelta(days=30)
        chain = pd.DataFrame([{"expiration": future, "strike": 100.0, "opt_kind": "call", "oi": 10,
                               "oi_change": 1, "volume": 1, "gxoi": 1.0, "dxoi": 1.0, "vxoi": 1.0,
                               "gamma": 0.0, "delta": 0.5, "iv": 0.2}])

        class _Source:
            def chain_long(self, symbol: str, **kw) -> pd.DataFrame:
                pulled.append(symbol)
                return chain

        class _Settings:
            index_roots = ["SPX", "SPY", "QQQ"]
            chain_exclude_roots = {"SPX", "SPY", "QQQ", "SPXW"}

        assert job.same_day_names(session, _Settings(), today) == ["NVDA", "AAPL"]
        job.run(session, _Source(), settings=_Settings())
    assert pulled == ["SPX", "SPY", "QQQ", "NVDA", "AAPL"]


def test_oi_chain_eod_replaces_the_morning_pull() -> None:
    from trading_intel.scheduler.jobs import oi_chain_eod as eod

    engine = create_engine("sqlite://")
    OiChainEod.__table__.create(engine)
    ts = eastern_now().replace(hour=0, minute=0, second=0, microsecond=0)
    future = pd.Timestamp(ts.date()) + pd.Timedelta(days=30)
    chain = pd.DataFrame([{"expiration": future, "strike": 100.0, "opt_kind": "call", "oi": 10,
                           "oi_change": 1, "volume": 1, "gxoi": 1.0, "dxoi": 1.0, "vxoi": 1.0,
                           "gamma": 0.0, "delta": 0.5, "iv": 0.2}])

    class _Source:
        def chain_long(self, symbol: str, **kw) -> pd.DataFrame:
            return chain

    class _Settings:
        chain_exclude_roots: set = set()

    with Session(engine) as session:
        session.add(OiChainEod(symbol="AAPL", ts=ts, expiry=future.date(), strike=105.0, cp="C",
                               dte=30, oi=7, source="convex_am"))
        session.commit()
        eod.run(session, _Source(), settings=_Settings(), symbols=["AAPL"])
        sources = sorted({r.source for r in session.scalars(select(OiChainEod)).all()})
    assert sources == ["convex_eod"]
