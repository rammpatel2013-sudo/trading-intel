"""Tests for ConvexClient — all mocked, no real API calls.

Shapes mirror the LIVE API:
- get_chain_as_rows -> ``[symbol, expiration, strike, kind, *params]``
  where ``expiration`` is days since the Unix epoch (e.g. 20595 = 2026-05-22).
- get_und           -> ``{"data": [[ [symbol, *vals], ... ]]}``  (rows at data[0])
"""
from __future__ import annotations

import sys
import types

import pandas as pd
import pytest

from trading_intel.config import Settings
from trading_intel.greeks.exposures import compute_exposures

# Data params ConvexClient requests, in order (== _CHAIN_PARAMS).
_DATA_PARAMS = (
    "delta", "gamma", "theta", "vega", "vanna", "charm", "volatility", "oi",
    "oi_ch", "day_volume", "gxoi", "dxoi", "vxoi", "multiplier",
)


def _row(*, symbol="SPY", expiration=20595, strike=730.0, kind="call", **param_overrides) -> list:
    """Build one get_chain_as_rows row: [symbol, expiration, strike, kind, *params]."""
    params = {
        "delta": 0.5, "gamma": 0.01, "theta": -0.1, "vega": 0.2, "vanna": 0.05,
        "charm": -0.02, "volatility": 0.18, "oi": 1000, "oi_ch": 25,
        "day_volume": 500, "gxoi": 1.0e6, "dxoi": 5.0e5, "vxoi": 2.0e5,
        "multiplier": 100,
    }
    params.update(param_overrides)
    return [symbol, expiration, strike, kind] + [params[p] for p in _DATA_PARAMS]


def _install_fake_convexlib(monkeypatch, chain_rows, und_price=734.29):
    mod = types.ModuleType("convexlib")
    api_mod = types.ModuleType("convexlib.api")

    class FakeConvexApi:
        def __init__(self, *args, **kwargs):
            pass

        def get_chain_as_rows(self, symbol, params, exps, rng):
            return chain_rows

        def get_und(self, symbols, params):
            # Mirror live shape: rows nested one level deeper, under data[0].
            rows = [
                [s] + [(und_price if p == "price" else 0.0) for p in params]
                for s in symbols
            ]
            return {"data": [rows], "meta": {"e": "1ms"}}

    api_mod.ConvexApi = FakeConvexApi
    mod.api = api_mod
    monkeypatch.setitem(sys.modules, "convexlib", mod)
    monkeypatch.setitem(sys.modules, "convexlib.api", api_mod)


def _make_client(monkeypatch, chain_rows, und_price=734.29):
    _install_fake_convexlib(monkeypatch, chain_rows, und_price)
    from trading_intel.clients.convex import ConvexClient

    return ConvexClient(Settings())


def test_exposures_returns_expected_shape(monkeypatch):
    rows = [
        _row(kind="call", strike=730.0),
        _row(kind="put", strike=740.0, gxoi=8.0e5, dxoi=-3.0e5),
    ]
    client = _make_client(monkeypatch, rows)

    result = client.exposures("SPY")

    expected_keys = {
        "symbol", "spot", "gex_total", "dex_total", "vex_total",
        "chex_total", "atm_iv", "gex_flip",
    }
    assert expected_keys.issubset(result.keys())
    assert result["symbol"] == "SPY"
    assert result["spot"] == pytest.approx(734.29)
    for key in ("gex_total", "dex_total", "vex_total", "chex_total"):
        assert isinstance(result[key], float)
    assert result["gex_flip"] is None or isinstance(result["gex_flip"], float)


def test_spot_unwraps_nested_und(monkeypatch):
    client = _make_client(monkeypatch, [_row()], und_price=738.77)
    df = client.underlying(["SPY"])
    assert "price" in df.columns
    assert df["price"].iloc[0] == pytest.approx(738.77)


def test_exposures_empty_chain_returns_empty(monkeypatch):
    client = _make_client(monkeypatch, [])
    assert client.exposures("SPY") == {}


def test_chain_layout_and_normalized_names(monkeypatch):
    client = _make_client(monkeypatch, [_row()])
    df = client.chain("SPY")
    for col in ("symbol", "expiration", "strike", "opt_kind"):
        assert col in df.columns
    assert "iv" in df.columns and "volume" in df.columns
    assert "oi_change" in df.columns and "oi_ch" not in df.columns
    assert "volatility" not in df.columns and "day_volume" not in df.columns
    assert df["opt_kind"].iloc[0] == "call"
    # epoch-day 20595 normalizes to a 2026 datetime
    assert pd.api.types.is_datetime64_any_dtype(df["expiration"])
    assert df["expiration"].iloc[0].year == 2026


def test_chain_bad_width_raises(monkeypatch):
    from trading_intel.errors import DataSourceError

    client = _make_client(monkeypatch, [[1, 2, 3]])  # wrong column count
    with pytest.raises(DataSourceError):
        client.chain("SPY")


def test_health_shape(monkeypatch):
    client = _make_client(monkeypatch, [_row()])
    health = client.health()
    assert health["vendor"] == "convexvalue"
    assert "consecutive_failures" in health


# ── Pure formula lock (no client, no mock) — raw net gxoi, matches Convex ──


def test_compute_exposures_locked_formulas():
    """Hand-computed values pin the GEX/DEX/VEX/CHEX formulas (raw net gxoi)."""
    chain = pd.DataFrame(
        [{
            "opt_kind": "call", "strike": 100.0, "gxoi": 1000.0, "dxoi": 500.0,
            "vanna": 0.1, "charm": -0.05, "oi": 200.0, "iv": 0.2,
        }]
    )
    out = compute_exposures(chain, spot=100.0)

    assert out["gex_total"] == pytest.approx(1000.0)                            # net signed gxoi
    assert out["dex_total"] == pytest.approx(500.0)
    assert out["vex_total"] == pytest.approx(0.1 * 200.0 * 100.0 * 0.2)         # 400
    assert out["chex_total"] == pytest.approx(-0.05 * 200.0 * 100.0 * 365.0)    # -365_000
    assert out["atm_iv"] == pytest.approx(0.2)


def test_compute_exposures_put_sign_flips_gex():
    chain = pd.DataFrame(
        [{
            "opt_kind": "put", "strike": 100.0, "gxoi": 1000.0, "dxoi": -500.0,
            "vanna": 0.1, "charm": -0.05, "oi": 200.0, "iv": 0.2,
        }]
    )
    out = compute_exposures(chain, spot=100.0)
    assert out["gex_total"] == pytest.approx(-1000.0)  # puts negative
    assert out["dex_total"] == pytest.approx(-500.0)
