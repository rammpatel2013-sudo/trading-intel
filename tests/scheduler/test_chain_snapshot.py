"""Tests for the per-strike chain-snapshot collector — pure, no Postgres.

``_chain_to_records`` is tested directly. ``run`` is exercised with a fake
source and a recording session (the Postgres ``pg_insert`` statement is captured,
not compiled), so no live DB/creds are needed.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
from sqlalchemy.dialects.postgresql.dml import Insert

from trading_intel.errors import DataSourceError
from trading_intel.scheduler.jobs.chain_snapshot import _chain_to_records, run

_TS = datetime(2026, 5, 22, 6, 45, tzinfo=UTC)


def _chain() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"expiration": pd.Timestamp("2026-06-18"), "strike": 7400.0, "opt_kind": "call",
             "delta": 0.5, "gamma": 0.01, "theta": -1.0, "vega": 2.0, "vanna": 0.3,
             "charm": -0.02, "iv": 0.15, "oi": 1200, "volume": 300, "gxoi": 5.0,
             "dxoi": 4.0, "vxoi": 3.0},
            {"expiration": pd.Timestamp("2026-06-18"), "strike": 7100.0, "opt_kind": "put",
             "delta": -0.4, "gamma": 0.02, "theta": -1.2, "vega": 2.2, "vanna": 0.1,
             "charm": -0.01, "iv": np.nan, "oi": 800, "volume": 150, "gxoi": -2.0,
             "dxoi": -1.5, "vxoi": -1.0},
            {"expiration": pd.NaT, "strike": 7000.0, "opt_kind": "call",
             "delta": 0.6, "gamma": 0.01, "theta": -1.0, "vega": 2.0, "vanna": 0.3,
             "charm": -0.02, "iv": 0.16, "oi": 10, "volume": 5, "gxoi": 1.0,
             "dxoi": 1.0, "vxoi": 1.0},
            {"expiration": pd.Timestamp("2026-06-18"), "strike": 7050.0, "opt_kind": "X",
             "delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "vanna": 0.0,
             "charm": 0.0, "iv": 0.1, "oi": 1, "volume": 1, "gxoi": 0.0,
             "dxoi": 0.0, "vxoi": 0.0},
        ]
    )


def test_chain_to_records_maps_and_filters():
    recs = _chain_to_records(_chain(), symbol="SPX", ts=_TS)
    assert len(recs) == 2  # NaT-expiry and unknown-side rows dropped

    call, put = recs
    assert call["symbol"] == "SPX" and call["ts"] == _TS and call["source"] == "convex"
    assert call["cp"] == "C" and put["cp"] == "P"
    assert call["expiry"].isoformat() == "2026-06-18"
    assert call["strike"] == 7400.0
    assert call["oi"] == 1200 and isinstance(call["oi"], int)
    assert call["volume"] == 300 and isinstance(call["volume"], int)
    assert call["cxoi"] is None  # feed has no cxoi
    assert put["iv"] is None  # NaN coerced to None


def test_chain_to_records_empty_and_missing_cols():
    assert _chain_to_records(pd.DataFrame(), symbol="SPX", ts=_TS) == []
    assert _chain_to_records(pd.DataFrame([{"foo": 1}]), symbol="SPX", ts=_TS) == []


class _RecordingSession:
    def __init__(self) -> None:
        self.executed: list = []
        self.commits = 0

    def execute(self, stmt) -> None:
        self.executed.append(stmt)

    def commit(self) -> None:
        self.commits += 1


class _FakeSource:
    def __init__(self, frames: dict) -> None:
        self.frames = frames
        self.calls: list[str] = []

    def chain(self, symbol: str, **_: object) -> pd.DataFrame:
        self.calls.append(symbol)
        val = self.frames[symbol]
        if isinstance(val, Exception):
            raise val
        return val

    def chain_long(self, symbol: str, **_: object) -> pd.DataFrame:
        # chain_snapshot now pulls the wide multi-expiry chain; delegate to chain.
        return self.chain(symbol)


class _Settings:
    def __init__(self, symbols: list[str]) -> None:
        self.watchlist_symbols = symbols
        self.CHAIN_SNAPSHOT_MAX_EXPS = 40
        self.CHAIN_SNAPSHOT_STRIKE_RANGE = 0.30


def test_run_orchestration_writes_skips_and_commits():
    source = _FakeSource(
        {"SPX": _chain(), "FAIL": DataSourceError("vendor down"), "EMPTY": pd.DataFrame()}
    )
    session = _RecordingSession()
    run(session, source, settings=_Settings(["SPX", "FAIL", "EMPTY"]))

    assert source.calls == ["SPX", "FAIL", "EMPTY"]
    # Only SPX produced rows -> exactly one INSERT (the effective-watchlist
    # resolver also issues a SELECT, which we ignore here).
    inserts = [e for e in session.executed if isinstance(e, Insert)]
    assert len(inserts) == 1
    assert session.commits == 1  # commit always called
