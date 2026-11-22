"""Correlation Fuse tab (scripts/fuse_tab.py) - 2026-11-22. Pure functions only, no network."""
from __future__ import annotations

import math
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fuse_tab as ft  # noqa: E402


def _days(n, start=date(2024, 1, 2)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _panel(n=260, stress_at=200):
    """Calm (low correlation, wide VIXEQ/VIX) then a correlation-bid stress leg from `stress_at`."""
    ds = _days(n)
    S = {k: {} for k in ft.NEED + ["MOVE", "HY", "SKEW"]}
    spx = 5000.0
    for i, d in enumerate(ds):
        k = i - stress_at
        stress = k >= 0
        vix = 14 + 0.3 * math.sin(i / 3) + (min(k, 10) * 2.2 if stress else 0)
        spx = spx * (1 - 0.006 if stress and k < 8 else 1 + 0.0006 * math.sin(i))
        S["VIX"][d] = vix
        S["VIXEQ"][d] = 38 + (min(k, 10) * 1.5 if stress else 0)
        S["COR1M"][d] = 8 + (min(k, 10) * 3 if stress else 0)
        S["COR3M"][d] = 10 + (min(k, 10) * 1.5 if stress else 0)
        S["COR90D"][d] = 30 + (min(k, 10) * 2 if stress else 0)
        S["COR10D"][d] = 5 + (min(k, 10) * 1.5 if stress else 0)
        S["VIX9D"][d] = vix - 1.5 + (3 if stress else 0)
        S["VIX3M"][d] = vix + 2 - (4 if stress else 0)
        S["VVIX"][d] = 85 + (20 if stress else 0)
        S["SPX"][d] = spx
        S["MOVE"][d] = 90.0
        S["SKEW"][d] = 140.0
        if i % 2 == 0:
            S["HY"][d] = 3.0
    return S


def test_parse_cboe_csv_reads_last_column_both_shapes():
    assert ft.parse_cboe_csv("DATE,OPEN,HIGH,LOW,CLOSE\n11/20/2026,55.17,62.51,46.16,52.16\n") == {"2026-11-20": 52.16}
    assert ft.parse_cboe_csv("DATE,VVIX\n11/20/2026,138.03\nbad,row\n") == {"2026-11-20": 138.03}


def test_align_forward_fills_sparse_series_and_starts_at_START(monkeypatch):
    monkeypatch.setattr(ft, "START", "2024-01-03")
    dates, X = ft.align(_panel(30))
    assert dates[0] == "2024-01-03"
    assert all(v == 3.0 for v in X["HY"])  # every other day in the input, filled forward
    assert len(X["VIX"]) == len(dates)


def test_compute_reads_calm_as_loaded_then_the_stress_as_transmitting():
    dates, X = ft.align(_panel())
    o = ft.compute(dates, X)
    s = o["series"]
    calm = s["phase"][150]
    assert calm == "LOADED", calm                       # F1 low corr + F2 wide ratio + F6 VVIX/VIX >= 5.5 + F7
    assert o["phase"] in ("TRANSMITTING", "RELEASING")
    assert o["score"]["T"] >= 3
    assert {p["id"] for p in o["points"]} == {p[0] for p in ft.POINTS}
    assert o["count"] == {"F": 8, "T": 7, "R": 7}
    for p in o["points"]:
        assert p["lo"] is None or p["hi"] is None or p["lo"] <= p["hi"]
    assert len(s["date"]) == len(s["F"]) == len(s["F1"]) == len(s["F1_on"])


def test_vix_cap_keeps_f5_and_f8_off_once_vix_is_high():
    dates, X = ft.align(_panel(n=207))
    o = ft.compute(dates, X)
    f8 = next(p for p in o["points"] if p["id"] == "F8")
    assert o["now"]["vix"] >= 22 and f8["now"] >= 15   # VIX over-reacted in the stress leg ...
    assert f8["on"] is False                           # ... but VIX is no longer calm, so it does not count


def test_release_needs_stress_first():
    dates, X = ft.align(_panel(stress_at=400, n=260))  # no stress at all
    o = ft.compute(dates, X)
    assert "RELEASING" not in o["series"]["phase"] and "TRANSMITTING" not in o["series"]["phase"]


def test_updown_years():
    spx = {"2026-01-02": 100.0, "2026-01-05": 101.0, "2026-01-06": 99.0}
    for i in range(30):
        spx[f"2026-02-{i % 28 + 1:02d}"] = 100 + (i % 2)
    out = ft.updown_years(spx)
    assert out and out[0]["year"] == "2026" and out[0]["up_days"] > 0 and out[0]["down_days"] > 0
