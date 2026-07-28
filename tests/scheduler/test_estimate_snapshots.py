"""Tests for the analyst-estimates snapshot job — pure, no network / no DB.

Covers the two things that broke in production: the nearest-future period pick,
the tolerant field extraction (stable FMP names ``epsAvg``/``revenueAvg``/
``numAnalystsEps``), and the resilient fetch path — CVForge primary with a
retry, then a direct-FMP fallback — that fixes the ``analyst-estimates`` 502.
"""

from __future__ import annotations

from datetime import date

from trading_intel.errors import DataSourceError
from trading_intel.scheduler.jobs.estimate_snapshots import (
    _extract,
    _fetch_estimates,
    _pick_period,
)


class _BoomForge:
    """CVForge stub whose fmp() always 502s (raises DataSourceError)."""

    def __init__(self) -> None:
        self.calls = 0

    def fmp(self, endpoint, params=None):
        self.calls += 1
        raise DataSourceError("CVForge GET /fmp/stable/analyst-estimates -> 502: <html>")


class _OkForge:
    """CVForge stub that returns a canned payload."""

    def __init__(self, payload) -> None:
        self._payload = payload
        self.calls = 0

    def fmp(self, endpoint, params=None):
        self.calls += 1
        return self._payload


class _FakeFmp:
    """Direct FMP client stub (the fallback)."""

    def __init__(self, payload) -> None:
        self._payload = payload
        self.calls = 0

    def analyst_estimates(self, ticker, *, period="annual", limit=8):
        self.calls += 1
        return self._payload


def test_pick_period_nearest_future():
    today = date(2026, 7, 28)
    payload = [
        {"date": "2025-09-27", "epsAvg": 6.1},
        {"date": "2027-09-25", "epsAvg": 7.9},
        {"date": "2026-09-26", "epsAvg": 7.1},
    ]
    d, rec = _pick_period(payload, today)
    assert d == date(2026, 9, 26) and rec["epsAvg"] == 7.1


def test_pick_period_falls_back_to_first_when_no_future():
    today = date(2026, 7, 28)
    payload = [{"date": "2024-09-28", "epsAvg": 5.0}]
    d, rec = _pick_period(payload, today)
    assert d == date(2024, 9, 28) and rec["epsAvg"] == 5.0


def test_extract_maps_stable_fields():
    row = _extract(
        "AAPL",
        [{"date": "2026-09-26", "epsAvg": 7.1, "revenueAvg": 4.2e11, "numAnalystsEps": 30}],
        as_of=date(2026, 7, 28),
    )
    assert row is not None
    assert row["symbol"] == "AAPL"
    assert row["eps_avg"] == 7.1 and row["revenue_avg"] == 4.2e11
    assert row["eps_num"] == 30 and row["period_date"] == date(2026, 9, 26)
    assert row["ts"] == date(2026, 7, 28) and row["source"] == "cvforge-fmp"


def test_fetch_uses_cvforge_when_ok():
    forge = _OkForge([{"date": "2026-09-26", "epsAvg": 7.1}])
    fmp = _FakeFmp([{"date": "2099-01-01", "epsAvg": 0.0}])
    out = _fetch_estimates(forge, fmp, "AAPL", pause=0.0)
    assert out == [{"date": "2026-09-26", "epsAvg": 7.1}]
    assert forge.calls == 1 and fmp.calls == 0  # no retry, no fallback needed


def test_fetch_retries_then_falls_back_to_fmp():
    forge = _BoomForge()
    fmp = _FakeFmp([{"date": "2026-09-26", "epsAvg": 7.1}])
    out = _fetch_estimates(forge, fmp, "AAPL", retries=1, pause=0.0)
    assert forge.calls == 2  # initial attempt + one retry on the 502
    assert fmp.calls == 1
    assert out is not None and out[0]["epsAvg"] == 7.1


def test_fetch_returns_none_when_all_sources_fail():
    forge = _BoomForge()
    out = _fetch_estimates(forge, None, "AAPL", retries=1, pause=0.0)
    assert out is None
    assert forge.calls == 2
