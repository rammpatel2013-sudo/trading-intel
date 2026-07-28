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


def test_shares_outstanding_from_shares_float():
    c = _client(
        {
            "stable/shares-float": _Resp(
                [
                    {
                        "symbol": "SOXL",
                        "date": "2026-07-14",
                        "floatShares": 133000000,
                        "outstandingShares": 135450060,
                        "source": "NASDAQ",
                    }
                ]
            )
        }
    )
    s = c.shares_outstanding("soxl")
    assert s is not None
    assert s.symbol == "SOXL" and s.shares_outstanding == 135450060
    assert s.float_shares == 133000000 and s.source == "NASDAQ"


def test_shares_outstanding_falls_back_to_quote():
    # shares-float yields no usable count -> fall back to /quote sharesOutstanding.
    c = _client(
        {
            "stable/shares-float": _Resp([]),
            "stable/quote": _Resp([{"symbol": "TSLQ", "sharesOutstanding": 4200000}]),
        }
    )
    s = c.shares_outstanding("TSLQ")
    assert s is not None and s.shares_outstanding == 4200000 and s.source == "quote"


def test_shares_outstanding_none_when_absent():
    # Nothing relevant mapped -> both endpoints degrade to None -> None.
    c = _client({"stable/news/stock": _Resp([])})
    assert c.shares_outstanding("ZZZZ") is None


def test_analyst_estimates_list_and_fields():
    c = _client(
        {
            "stable/analyst-estimates": _Resp(
                [
                    {
                        "date": "2027-09-25",
                        "epsAvg": 7.9,
                        "revenueAvg": 4.6e11,
                        "numAnalystsEps": 30,
                    },
                    {
                        "date": "2026-09-26",
                        "epsAvg": 7.1,
                        "revenueAvg": 4.2e11,
                        "numAnalystsEps": 32,
                    },
                ]
            )
        }
    )
    out = c.analyst_estimates("AAPL")
    assert len(out) == 2
    assert out[0]["epsAvg"] == 7.9 and out[1]["revenueAvg"] == 4.2e11


def test_analyst_estimates_degrades_to_empty():
    # analyst-estimates unmapped -> KeyError caught in _get -> [] (never raises).
    c = _client({"stable/profile": _Resp([{"companyName": "x"}])})
    assert c.analyst_estimates("AAPL") == []
