"""run_skew_scan: symbol parsing, background job + saved result, named-mode scan (2026-11-13)."""
import time
from datetime import date, timedelta
from types import SimpleNamespace

import numpy as np
import pandas as pd

from trading_intel.mcp import skew_scan_tool as sst


class _FakeMCP:
    def tool(self):
        return lambda f: f


def test_parse_symbols():
    assert sst.parse_symbols("amd or ttd") == ["AMD", "TTD"]
    assert sst.parse_symbols("$NVDA, hood and NVDA") == ["NVDA", "HOOD"]
    assert sst.parse_symbols("") == [] and sst.parse_symbols(["pins"]) == ["PINS"]


def test_slow_run_returns_running_then_result(tmp_path):
    def slow(syms, prog):
        prog.update(stage="skew", done=0, of=1)
        time.sleep(0.6)
        return {"status": "done", "names": {s: {"verdict": "NOT READY"} for s in syms}}

    fn = sst.register(_FakeMCP(), SimpleNamespace(), None, scanner=slow, wait_s=0.1, save_dir=tmp_path)
    first = fn("AMD")
    assert first["status"] == "running" and first["stage"] == "skew"
    time.sleep(0.8)
    second = fn("AMD")
    assert second["status"] == "done" and "AMD" in second["names"]
    assert list(tmp_path.glob("*_AMD.json"))
    again = sst.register(_FakeMCP(), SimpleNamespace(), None, scanner=slow, wait_s=0.1, save_dir=tmp_path)("amd")
    assert again.get("from_saved_run") is True


def test_error_is_reported(tmp_path):
    def boom(syms, prog):
        raise RuntimeError("vendor down")

    fn = sst.register(_FakeMCP(), SimpleNamespace(), None, scanner=boom, wait_s=1.0, save_dir=tmp_path)
    assert fn("AMD") == {"status": "error", "error": "vendor down"}


class _FakeHist:
    """Prices slide 15% into new lows; RR collapses over the last 5 sessions -> sd-v1 fires today."""

    def __init__(self):
        d, self.days = date(2025, 9, 1), []
        while len(self.days) < 290:
            if d.weekday() < 5:
                self.days.append(d)
            d += timedelta(days=1)
        rng = np.random.default_rng(3)
        px = 100 + np.cumsum(rng.normal(0, 0.3, len(self.days)))
        px[-12:] = px[-13] * np.linspace(0.985, 0.85, 12)
        self.px = dict(zip(self.days, px))
        rr = 3 + rng.normal(0, 0.4, len(self.days))
        rr[-5:] = rr[-6] + np.linspace(-1.2, -6, 5)
        self.rr = dict(zip(self.days, rr))

    def closes(self, sym, frm, to, adjusted=None):
        return self.px if sym == "AMD" else {}

    def update(self, sym, raw, start, **_):
        return pd.DataFrame({"date": self.days, "ivp25": [0.4 + self.rr[d] / 100 for d in self.days],
                             "ivc25": [0.4] * len(self.days)})


def test_named_scan_reads_each_symbol(monkeypatch):
    monkeypatch.setattr(sst, "eastern_now", lambda: pd.Timestamp("2026-11-13 18:00").to_pydatetime())
    out = sst.scan(SimpleNamespace(chain_exclude_roots=set()), None, ["AMD", "TTD"], {}, hist=_FakeHist())
    assert out["status"] == "done" and out["rule"].startswith("sd-v1")
    assert out["names"]["AMD"]["verdict"].startswith("READY")
    assert out["names"]["TTD"]["verdict"].startswith("CAN'T SCORE")


def test_watchlist_mode_lists_ready_names(monkeypatch):
    monkeypatch.setattr(sst, "eastern_now", lambda: pd.Timestamp("2026-11-13 18:00").to_pydatetime())
    monkeypatch.setattr(sst, "_watchlist", lambda sf, ex: ["AMD", "FLAT"])
    fh = _FakeHist()
    flat = {d: 50.0 + i * 0.01 for i, d in enumerate(fh.days)}
    fh.closes = lambda sym, frm, to, adjusted=None: fh.px if sym == "AMD" else flat
    out = sst.scan(SimpleNamespace(chain_exclude_roots=set()), None, [], {}, hist=fh)
    assert list(out["ready"]) == ["AMD"] and out["checked"]["passed_price_check"] == 1
    assert out["checked"]["watchlist_names"] == 2 and out["not_finished"] == []
