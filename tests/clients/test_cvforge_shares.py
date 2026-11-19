"""CVForge shares_outstanding (2026-11-19 LETF fix: the direct FMP key was empty on the NAS)."""

from __future__ import annotations

from trading_intel.clients.cvforge import CVForgeClient
from trading_intel.errors import DataSourceError


def _client(resp):
    c = CVForgeClient.__new__(CVForgeClient)
    c.fmp = resp   # type: ignore[method-assign]
    return c


def test_shares_from_passthrough() -> None:
    c = _client(lambda ep, p: [{"date": "2026-11-19 16:39:20", "floatShares": None, "outstandingShares": 3755723871,
                                "symbol": "TSLL"}] if ep == "shares-float" and p == {"symbol": "TSLL"} else None)
    s = c.shares_outstanding("TSLL")
    assert s.symbol == "TSLL" and s.shares_outstanding == 3755723871 and s.source == "cvforge_fmp"
    assert s.as_of.isoformat() == "2026-11-19"


def test_shares_none_on_error_or_empty() -> None:
    def boom(ep, p):
        raise DataSourceError("502")
    assert _client(boom).shares_outstanding("SOXL") is None
    assert _client(lambda ep, p: []).shares_outstanding("SOXL") is None
    assert _client(lambda ep, p: [{"outstandingShares": 0}]).shares_outstanding("SOXL") is None
