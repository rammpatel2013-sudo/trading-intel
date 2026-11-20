"""Tests for market/week_map.py (2026-11-20): calendar targets and the path / level directions."""
from __future__ import annotations

from datetime import date, datetime

import numpy as np

from trading_intel.market import clean_book as cb
from trading_intel.market import week_map as wm


def test_weekly_target_rolls_on_friday_and_skips_holidays() -> None:
    assert wm.weekly_target(date(2026, 11, 18)) == date(2026, 11, 20)
    assert wm.weekly_target(date(2026, 11, 20)) == date(2026, 11, 27)
    assert wm.weekly_target(date(2027, 3, 22)) == date(2027, 3, 25)     # Good Friday 3/26 -> Thursday


def test_monthly_target_is_next_third_friday() -> None:
    assert wm.monthly_target(date(2026, 11, 19)) == date(2026, 11, 20)
    assert wm.monthly_target(date(2026, 11, 20)) == date(2026, 12, 18)
    assert wm.monthly_target(date(2026, 12, 21)) == date(2027, 1, 15)


def test_session_days_skip_thanksgiving() -> None:
    assert wm.session_days(date(2026, 11, 23), date(2026, 11, 27)) == [
        date(2026, 11, 23), date(2026, 11, 24), date(2026, 11, 25), date(2026, 11, 27)]


def _rows(fwd: float = 6000.0, exp: date = date(2026, 11, 27)) -> list[dict]:
    """A put-heavy book with real-looking mid prices (from a 20% smile) so clean_book finds a forward."""
    t = cb.years(datetime(2026, 11, 23, 8, 35), cb.settle_dt(exp, False))
    rows = []
    for k in np.arange(5600, 6401, 25.0):
        for cp in ("C", "P"):
            px = float(cb.b76(fwd, np.array([k]), np.array([0.20]), t, np.array([cp == "C"]))[0])
            oi = (40_000 if k == 5900 else 4_000) if cp == "P" else 2_000
            rows.append({"expiry": exp, "strike": float(k), "cp": cp, "oi": oi, "bid": px * 0.99,
                         "ask": px * 1.01 + 0.05, "source": "convex_am", "iv": 0.2})
    return rows


def test_map_paths_line_and_flat_path() -> None:
    m = wm.build_map(_rows(), datetime(2026, 11, 23, 8, 35), date(2026, 11, 27), spot=6000.0)
    assert m["found"]
    assert [d["date"] for d in m["days"]] == ["2026-11-23", "2026-11-24", "2026-11-25", "2026-11-27"]
    assert set(m["paths"]["flat"]) == {m["forward"]}                     # vol unchanged stays put
    assert m["ends"]["up"] < m["forward"] < m["ends"]["down"]           # dealers short puts: vol up -> sell
    assert m["levels"]["line"]["strike"] == 5900.0                       # the big put strike
    for d in m["days"]:
        assert d["cone"][0] < m["forward"] < d["cone"][1]
    assert m["am_settle"] is None


def test_vol_shift_caps() -> None:
    assert wm.vol_shift(1.0, 2) == 2 * wm.VOL_STEP
    assert wm.vol_shift(-1.0, 50) == -wm.VOL_CAP
