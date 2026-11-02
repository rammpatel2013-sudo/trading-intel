"""Breadth job dating (2026-10-29): never file the previous session's bars under today."""

from __future__ import annotations

from datetime import date, datetime

from trading_intel.scheduler.jobs import breadth as job

THU = date(2026, 10, 29)


class _FakeFmp:
    """sp500-constituent + light history whose newest bar is ``newest``; no holdings."""

    def __init__(self, newest: str):
        self.newest = newest

    def fmp(self, endpoint, params=None):
        if endpoint == "sp500-constituent":
            return [{"symbol": f"S{i}"} for i in range(5)]
        if endpoint == "historical-price-eod/light":
            return [{"date": self.newest, "price": 100.0 - i * 0.1} for i in range(80)]
        return []


class _NoDb:
    def __getattr__(self, name):  # the stale path must return before touching the DB
        raise AssertionError(f"session.{name} used on a stale-source day")


def test_stale_vendor_bars_skip_the_row(monkeypatch):
    monkeypatch.setattr(job, "eastern_now", lambda: datetime(2026, 10, 29, 16, 30))
    monkeypatch.setattr(job, "bank_small_caps", lambda *a, **k: None)
    out = job.run(_NoDb(), settings=None, client=_FakeFmp("2026-10-28"))
    assert out["skipped"] == "source_stale"
    assert out["data_day"] == "2026-10-28" and out["as_of"] == "2026-10-29"


def test_spx_close_only_when_the_bar_is_today(monkeypatch):
    from trading_intel.market import norseman as nm
    from trading_intel.strategies import norseman_regime as nr

    bars = [nm.Bar(date(2026, 10, 27), 7700.0, 7600.0, 7650.0),
            nm.Bar(date(2026, 10, 28), 7720.0, 7610.0, 7660.0)]
    monkeypatch.setattr(nr, "spx_bars", lambda s, as_of: bars)
    highs, close = job._spx_line_inputs(None, THU)
    assert highs == [7700.0, 7720.0] and close is None          # newest bar is yesterday
    highs, close = job._spx_line_inputs(None, date(2026, 10, 28))
    assert close == 7660.0
