"""Tests for long-dated rolling GEX + the client's wide-expiration fallback."""
from __future__ import annotations

import sys
import types
from datetime import date

import pandas as pd
import pytest

from trading_intel.config import Settings
from trading_intel.greeks.rolling import compute_rolling_gex

_DATA_PARAMS = (
    "delta", "gamma", "theta", "vega", "vanna", "charm", "volatility", "oi",
    "day_volume", "gxoi", "dxoi", "vxoi", "multiplier",
)


def _chain(ref: date) -> pd.DataFrame:
    """Two expirations: 30 days and 200 days out (datetimes, as the client emits)."""
    e1 = pd.Timestamp(ref) + pd.Timedelta(days=30)
    e2 = pd.Timestamp(ref) + pd.Timedelta(days=200)
    return pd.DataFrame([
        {"opt_kind": "call", "expiration": e1, "gxoi": 100.0},
        {"opt_kind": "put", "expiration": e1, "gxoi": 40.0},
        {"opt_kind": "call", "expiration": e2, "gxoi": 50.0},
    ])


def test_rolling_window_excludes_beyond_horizon():
    ref = date(2026, 5, 21)
    out = compute_rolling_gex(_chain(ref), window_days=180, ref=ref)
    # 200-day expiration excluded; near exp net = 100 - 40 = 60
    assert out["n_expirations"] == 1
    assert out["total"] == pytest.approx(60.0)
    assert out["term"][0]["dte"] == 30
    assert out["term"][0]["gex"] == pytest.approx(60.0)


def test_rolling_includes_within_wider_window():
    ref = date(2026, 5, 21)
    out = compute_rolling_gex(_chain(ref), window_days=365, ref=ref)
    assert out["n_expirations"] == 2
    assert out["total"] == pytest.approx(110.0)  # 60 + 50
    assert [t["dte"] for t in out["term"]] == [30, 200]


def test_rolling_empty_chain():
    assert compute_rolling_gex(pd.DataFrame(), window_days=180) == {
        "total": 0.0, "n_expirations": 0, "term": []
    }


# ── chain_long defensive fallback ───────────────────────────────────────


def _row(expiration=20595, kind="call", **ov):
    params = {
        "delta": 0.5, "gamma": 0.01, "theta": -0.1, "vega": 0.2, "vanna": 0.05,
        "charm": -0.02, "volatility": 0.18, "oi": 1000, "day_volume": 500,
        "gxoi": 1.0e6, "dxoi": 5.0e5, "vxoi": 2.0e5, "multiplier": 100,
    }
    params.update(ov)
    return ["SPY", expiration, 730.0, kind] + [params[p] for p in _DATA_PARAMS]


def test_chain_long_falls_back_when_wide_exps_rejected(monkeypatch):
    mod = types.ModuleType("convexlib")
    api_mod = types.ModuleType("convexlib.api")

    class FakeConvexApi:
        def __init__(self, *a, **k):
            pass

        def get_chain_as_rows(self, symbol, params, exps, rng):
            # Simulate the vendor rejecting too-wide expiration requests.
            if len(exps) > 12:
                raise RuntimeError("400 Bad Request: too many exps")
            return [_row()]

    api_mod.ConvexApi = FakeConvexApi
    mod.api = api_mod
    monkeypatch.setitem(sys.modules, "convexlib", mod)
    monkeypatch.setitem(sys.modules, "convexlib.api", api_mod)

    from trading_intel.clients.convex import ConvexClient

    client = ConvexClient(Settings())
    df = client.chain_long("SPY")  # 40,30,20 rejected → 12 succeeds
    assert not df.empty
    assert "gxoi" in df.columns
