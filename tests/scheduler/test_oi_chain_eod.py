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
