"""tests for scripts/tdex_read.py + the Risk-page TailDex card (2026-11-22)."""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import tdex_read  # noqa: E402


def test_classify():
    assert tdex_read.classify(20, 40, -20, 10) == "Normal"
    assert tdex_read.classify(30, 25, -2, 0) == "Pre-hedge"
    assert tdex_read.classify(31, 36, -11.6, 9.8) == "Flush"
    assert tdex_read.classify(26, 33, -14, 2.9) == "Elevated"


def _series(n=400):
    d0 = date(2020, 1, 1)
    ds = [(d0 + timedelta(days=i)).isoformat() for i in range(n)]
    tdx = {d: [None, None, None, 30.0 if i == n - 1 else 12.0] for i, d in enumerate(ds)}
    vix = {d: 36.0 if i == n - 1 else 15.0 for i, d in enumerate(ds)}
    spx = {d: 100.0 - (12 if i == n - 1 else 0) for i, d in enumerate(ds)}
    v9 = {d: 40.0 for d in ds}
    v3 = {d: 30.0 for d in ds}
    return tdx, vix, spx, v9, v3


def test_build_flush_today():
    o = tdex_read.build(*_series())
    assert o["read"] == "Flush" and o["pct"] >= 99 and o["dd"] == -12.0
    assert o["episodes"][-1]["date"] == o["asof"] and len(o["s_tdex"]) == 252


def test_card_renders():
    from risk_page_tdex import tdex_body
    h = tdex_body(tdex_read.build(*_series()))
    assert "Flush" in h and "<svg" in h
    assert tdex_body({}) == ""
