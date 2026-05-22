"""Tests for ConvexClient time-and-sales + OCC symbol parsing — all mocked.

The live ``/api/data/tas`` response is ``{"data": [<header>, [<rows>]], ...}``
with row values aligned to the requested ``cols``. tas auth is the session
cookie (no Bearer/runtime token), so we stub ``self._api.session.post``.
"""
from __future__ import annotations

import sys
import types

import pandas as pd
import pytest

from trading_intel.clients.convex import parse_occ_symbol
from trading_intel.config import Settings


# ── parse_occ_symbol (pure) ──────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("sym", "root", "year", "month", "day", "strike", "kind"),
    [
        (".SPXW260522C7400", "SPXW", 2026, 5, 22, 7400.0, "call"),
        (".SPX261120P7150", "SPX", 2026, 11, 20, 7150.0, "put"),
        ("SPY260618C530.5", "SPY", 2026, 6, 18, 530.5, "call"),
    ],
)
def test_parse_occ_symbol_ok(sym, root, year, month, day, strike, kind):
    r, exp, stk, k = parse_occ_symbol(sym)
    assert r == root
    assert (exp.year, exp.month, exp.day) == (year, month, day)
    assert stk == pytest.approx(strike)
    assert k == kind


@pytest.mark.parametrize("sym", ["", "SPX", "GARBAGE", None, "SPXW260522X7400"])
def test_parse_occ_symbol_non_option_returns_none(sym):
    assert parse_occ_symbol(sym) == (None, None, None, None)


# ── time_and_sales (mocked session) ──────────────────────────────────────────
class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, payload):
        self._payload = payload
        self.last_url = None
        self.last_json = None

    def post(self, url, json=None, timeout=None):
        self.last_url = url
        self.last_json = json
        return _FakeResp(self._payload)


def _client_with_tas(monkeypatch, payload):
    mod = types.ModuleType("convexlib")
    api_mod = types.ModuleType("convexlib.api")

    session = _FakeSession(payload)

    class FakeConvexApi:
        def __init__(self, *args, **kwargs):
            self.session = session

    api_mod.ConvexApi = FakeConvexApi
    mod.api = api_mod
    monkeypatch.setitem(sys.modules, "convexlib", mod)
    monkeypatch.setitem(sys.modules, "convexlib.api", api_mod)
    from trading_intel.clients.convex import ConvexClient

    return ConvexClient(Settings()), session


def _tas_payload():
    header = ["time", "symbol", "price", "size", "value", "aggressor_side"]
    rows = [
        [1779409448959, ".SPX261120P7150", 211.53, 25.0, 528825.0, "buy"],
        [1779411297501, ".SPXW260522C7400", 74.3, 50.0, 371500.0, "sell"],
    ]
    return {"data": [header, rows], "meta": {"e": "157ms"}}


def test_time_and_sales_parses_and_normalizes(monkeypatch):
    client, session = _client_with_tas(monkeypatch, _tas_payload())
    df = client.time_and_sales("SPX", limit=5)

    # request shaped correctly: single-symbol list under `s`, required fields present
    assert session.last_url.endswith("/api/data/tas")
    assert session.last_json["s"] == ["SPX"]
    for field in ("cols", "limit", "asc", "orderby", "filters", "day", "futs"):
        assert field in session.last_json

    # value->premium, volatility->iv rename; OCC parsed into structured cols
    assert "premium" in df.columns and "value" not in df.columns
    assert {"root", "expiration", "strike", "opt_kind"}.issubset(df.columns)
    assert df["opt_kind"].tolist() == ["put", "call"]
    assert df["strike"].tolist() == [7150.0, 7400.0]
    assert df["root"].tolist() == ["SPX", "SPXW"]
    assert pd.api.types.is_datetime64_any_dtype(df["time"])
    # epoch-ms (UTC) converted to US/Eastern: 1779409448959 -> 2026-05-21 20:24 ET
    assert str(df["time"].dt.tz) == "America/New_York"
    assert df["time"].iloc[0].hour == 20
    assert df["aggressor_side"].tolist() == ["buy", "sell"]


def test_time_and_sales_empty_returns_empty_frame(monkeypatch):
    client, _ = _client_with_tas(monkeypatch, {"data": [["time", "symbol"], []]})
    df = client.time_and_sales("SPX")
    assert df.empty
    assert {"root", "expiration", "strike", "opt_kind"}.issubset(df.columns)
