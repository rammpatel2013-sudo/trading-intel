"""Tests for the FMP client (stable API; injected HTTP, no network)."""

from __future__ import annotations

from pydantic import SecretStr

from trading_intel.clients.fmp import FmpClient


class _Resp:
    def __init__(self, json_data):
        self._json = json_data

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


class _Client:
    """Returns a canned response for the first URL substring that matches."""

    def __init__(self, mapping):
        self._mapping = mapping

    def get(self, url, params=None):
        for key, resp in self._mapping.items():
            if key in url:
                return resp
        raise KeyError(url)


class _Settings:
    FMP_API = SecretStr("test-key")


def _client(mapping) -> FmpClient:
    return FmpClient(_Settings(), client=_Client(mapping))


def test_profile_uses_stable_and_returns_first():
    c = _client({"stable/profile": _Resp([{"companyName": "Apple Inc.", "sector": "Technology"}])})
    p = c.profile("AAPL")
    assert p is not None and p["companyName"] == "Apple Inc."


def test_income_statement_list():
    c = _client({"stable/income-statement": _Resp([{"revenue": 1}, {"revenue": 2}])})
    out = c.income_statement("AAPL")
    assert len(out) == 2 and out[0]["revenue"] == 1


def test_news_and_failure_degrades():
    # Only the news endpoint is mapped; unmapped calls -> KeyError caught -> None/[].
    c = _client({"stable/news/stock": _Resp([{"title": "Apple ships chips"}])})
    assert c.news("AAPL")[0]["title"] == "Apple ships chips"
    assert c.profile("AAPL") is None
    assert c.income_statement("AAPL") == []
