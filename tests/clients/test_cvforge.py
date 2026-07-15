"""Unit tests for CVForgeClient -- all mocked, no real HTTP.

The canned ``/chains`` response mirrors the live shape: a grouped payload where
each strike entry is ``[strike_float, call_row, put_row]`` and each *row* is a
positional array in ``params`` order.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from trading_intel.clients.cvforge import CVForgeClient, _flatten_chain
from trading_intel.config import Settings

_PARAMS = [
    "ticker",
    "expiration_date",
    "strike_price",
    "contract_type",
    "delta",
    "gamma",
    "theta",
    "vega",
    "implied_volatility",
    "open_interest",
    "day_volume",
    "underlying_price",
]
_EXP = "2099-12-18"  # far future so years_to_expiry stays positive
_CALL = ["O:X_C", _EXP, 100.0, "call", 0.55, 0.02, -0.10, 0.20, 0.30, 1000, 500, 101.0]
_PUT = ["O:X_P", _EXP, 100.0, "put", -0.45, 0.02, -0.10, 0.20, 0.32, 800, 400, 101.0]


def _resp() -> dict:
    return {
        "symbol": "X",
        "params": list(_PARAMS),
        "chain": [{"expiration": _EXP, "strikes": [[100.0, _CALL, _PUT]]}],
    }


def _client() -> CVForgeClient:
    return CVForgeClient(Settings(CVFORGE_API_KEY=SecretStr("test-key")))


def test_flatten_chain_pairs_call_and_put():
    rows = _flatten_chain(_resp())
    assert len(rows) == 2
    assert {r["opt_kind"] for r in rows} == {"call", "put"}
    call = next(r for r in rows if r["opt_kind"] == "call")
    assert call["strike"] == 100.0
    assert call["iv"] == 0.30
    assert call["oi"] == 1000
    assert call["underlying_price"] == 101.0


def test_chain_synthesizes_greeks(monkeypatch):
    client = _client()
    monkeypatch.setattr(client, "_get", lambda *a, **k: _resp())
    df = client.chain("X")
    assert len(df) == 2
    for col in ("vanna", "charm", "gxoi", "dxoi", "vxoi", "oi_change"):
        assert col in df.columns
    call = df[df["opt_kind"] == "call"].iloc[0]
    assert call["gxoi"] == pytest.approx(0.02 * 1000)  # gamma * oi
    assert call["dxoi"] == pytest.approx(0.55 * 1000)  # signed delta * oi
    put = df[df["opt_kind"] == "put"].iloc[0]
    assert put["dxoi"] == pytest.approx(-0.45 * 800)
    assert df["oi_change"].isna().all()
    client.close()


def test_exposures_reuses_passed_chain(monkeypatch):
    """exposures(chain=...) must NOT re-pull /chains -- the double-pull fix."""
    client = _client()
    calls = {"n": 0}

    def fake_get(*a, **k):
        calls["n"] += 1
        return _resp()

    monkeypatch.setattr(client, "_get", fake_get)
    chain = client.chain("X")  # 1 _get
    result = client.exposures("X", chain=chain)  # reuse -> no second pull
    assert calls["n"] == 1
    assert result.get("symbol") == "X"
    assert "spot" in result
    client.close()


def test_chain_empty_response_is_empty(monkeypatch):
    client = _client()
    monkeypatch.setattr(client, "_get", lambda *a, **k: {"params": list(_PARAMS), "chain": []})
    assert client.chain("X").empty
    client.close()
