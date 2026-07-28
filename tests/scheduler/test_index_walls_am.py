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
        def chain_long(self, symbol: str) -> pd.DataFrame:
            return chain

    class _Settings:
        index_roots = ["SPX"]

    with Session(engine) as session:
        job.run(session, _Source(), settings=_Settings(), symbols=["SPX"])
        rows = session.scalars(select(OiChainEod).where(OiChainEod.symbol == "SPX")).all()

    assert len(rows) == 2
    assert {r.strike for r in rows} == {7500.0, 7400.0}
    assert all(r.source == "convex_am" for r in rows)


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
