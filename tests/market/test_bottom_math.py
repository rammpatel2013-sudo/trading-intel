"""bottom_math (2026-11-13 overnight): Elder, MACD/PPO, VIX osc, FTD, washout, VIX-shock base rate."""
from datetime import date, timedelta

import pytest

from trading_intel.market import bottom_math as bm


def test_macd_hist_and_elder_on_trend():
    up = [100 + 0.05 * i * i for i in range(60)]          # accelerating rise
    assert bm.elder(up) == "green"
    down = [400 - 0.05 * i * i for i in range(60)]        # accelerating fall
    assert bm.elder(down) == "red"
    assert bm.elder(up[:10]) is None
    assert bm.ppo(up)[-1] > 0


def test_vix_osc_spike():
    v = [15.0] * 9 + [15.0, 27.0]
    o = bm.vix_osc(v)
    assert o[-1] == pytest.approx(100 * (27 / ((15 * 9 + 27) / 10) - 1))


def test_follow_through_day_found_and_given_back():
    closes = [100, 98, 96, 94, 92, 92.5, 93, 92.8, 94.6, 94.0]   # low 92 (i4); day1 i5; i8 = day 4 +1.94%
    vols = [1] * 8 + [5, 2]
    f = bm.follow_through(closes, vols)
    assert f["state"] == "follow-through" and f["ftd_idx"] == 8
    assert f["ftd_gave_back"] == pytest.approx((94.6 - 94.0) / (94.6 - 92.8))
    assert bm.follow_through([1, 2, 3], [None] * 3)["state"] == "no data"


def test_follow_through_needs_higher_volume():
    closes = [100, 98, 96, 94, 92, 92.5, 93, 92.8, 94.6]
    f = bm.follow_through(closes, [5] * 8 + [1])
    assert f["state"] == "rally attempt"


def test_washout_items():
    w = bm.washout(spy_vol=[100.0] * 20 + [350.0], up_share=[0.09, 0.5, 0.91],
                   spx_lows=[10, 9, 8, 7, 6, 7, 8, 9, 8, 5.5], vix_closes=[20, 22, 25, 28, 30, 27, 25, 24, 23, 22],
                   open_=98.0, low=97.0, close=100.0, prev_close=99.0, pct50=[15, 12, 18])
    it = w["items"]
    assert it["volume climax"] == 1.0 and it["90% down then 90% up"] == 1.0
    assert it["VIX divergence"] == 1.0 and it["retail capitulation"] is None
    assert w["score"] >= 4


def test_shock_base_rate():
    d0 = date(2026, 1, 1)
    dates = [d0 + timedelta(days=i) for i in range(40)]
    vix = [15.0] * 10 + [22.0] + [16.0] * 29
    spx = [100.0 + i for i in range(40)]
    r = bm.shock_base_rate(dates, vix, spx)
    assert r["n"] == 1 and r["shock_20d_n"] == 1 and r["shock_20d_up"] == 1.0


def test_weekly_last_close():
    d0 = date(2026, 11, 2)  # Monday
    pairs = [(d0 + timedelta(days=i), float(i)) for i in range(12)]
    assert bm.weekly(pairs) == [6.0, 11.0]
