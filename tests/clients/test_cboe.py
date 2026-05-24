"""Tests for the CBOE client (fake httpx-like client, no network)."""

from __future__ import annotations

from trading_intel.clients.cboe import CboeClient


class FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self._status = status

    def raise_for_status(self):
        if self._status >= 400:
            raise RuntimeError(f"HTTP {self._status}")

    def json(self):
        return self._payload


class FakeHttp:
    """Routes by the `/{sym}.json` suffix so _VIX vs _VIX3M don't collide."""

    def __init__(self, by_sym: dict):
        self._by_sym = by_sym

    def get(self, url: str):
        for sym, payload in self._by_sym.items():
            if f"/{sym}.json" in url:
                return FakeResp(payload)
        return FakeResp({}, status=404)


def test_vvix_parsed_from_current_price():
    http = FakeHttp({"_VVIX": {"data": {"current_price": 95.3}}})
    assert CboeClient(client=http).vvix() == 95.3


def test_term_structure_orders_and_parses():
    http = FakeHttp(
        {
            "_VIX9D": {"data": {"current_price": 18.0}},
            "_VIX": {"data": {"last": 20.0}},
            "_VIX3M": {"data": {"close": 22.0}},
            "_VIX6M": {"data": {"value": 23.0}},
        }
    )
    term = CboeClient(client=http).term_structure()
    assert term == {"VIX9D": 18.0, "VIX": 20.0, "VIX3M": 22.0, "VIX6M": 23.0}


def test_parse_price_fallback_keys_and_none():
    assert CboeClient._parse_price({"data": {"price": 1.5}}) == 1.5
    assert CboeClient._parse_price({"current_price": 2.0}) == 2.0  # no "data" wrapper
    assert CboeClient._parse_price({"data": {"unknown": 9}}) is None
    assert CboeClient._parse_price(None) is None


def test_fetch_failure_degrades_to_none():
    http = FakeHttp({})  # every symbol 404s
    assert CboeClient(client=http).vvix() is None
