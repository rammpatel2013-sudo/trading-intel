"""Unit tests for the Bull/Bear Line + A-D line + McClellan + divergence additions
to ``market.breadth`` (the regime half — pure, no vendor)."""
from __future__ import annotations

from datetime import date

from trading_intel.market import breadth as bm


def test_bull_bear_line_is_ten_pct_below_running_max():
    assert bm.bull_bear_line([100.0, 90.0, 110.0, 105.0]) == 110.0 * 0.90
    # ratchets on the max, ignores the later lower close
    assert bm.bull_bear_line([200.0, 150.0]) == 180.0
    assert bm.bull_bear_line([]) is None


def test_weekly_last_closes_takes_last_close_per_iso_week():
    # two days in ISO week 2 of 2026, one in week 3 → [wk2 last, wk3]
    rows = [
        (date(2026, 1, 6), 10.0),  # Tue wk2
        (date(2026, 1, 9), 12.0),  # Fri wk2  ← last of wk2
        (date(2026, 1, 13), 15.0),  # Tue wk3
    ]
    assert bm.weekly_last_closes(rows) == [12.0, 15.0]


def test_ad_line_cumulates():
    assert bm.ad_line_next(1000, 300, 200) == 1100
    assert bm.ad_line_next(None, 300, 200) == 100  # seeds at net
    assert bm.ad_line_next(1100, 100, 400) == 800  # can fall


def test_mcclellan_needs_two_points_and_returns_floats():
    osc, summ = bm.mcclellan([50.0])
    assert osc is None and summ is None
    series = [100.0, -50.0, 80.0, -20.0, 60.0, 10.0]
    osc, summ = bm.mcclellan(series, prev_summation=500.0)
    assert isinstance(osc, float)
    assert summ == 500.0 + osc  # summation = prior + today's oscillator


def test_divergence_confirming_when_both_make_new_highs():
    price = [100, 101, 102, 103, 104]
    adln = [10, 11, 12, 13, 14]
    d = bm.breadth_divergence(price, adln)
    assert d["state"] == "confirming"


def test_divergence_bearish_when_price_highs_but_breadth_lags():
    # price keeps making new highs; A-D line rolls over (the top-warning gap)
    price = [100, 101, 102, 103, 104, 105]
    adln = [20, 21, 22, 21, 20, 19]
    d = bm.breadth_divergence(price, adln)
    assert d["state"] == "bearish_div"
    assert d["length"] >= 2


def test_divergence_bullish_when_price_lows_but_breadth_firmer():
    price = [100, 99, 98, 97, 96, 95]
    adln = [10, 9, 8, 9, 10, 11]
    d = bm.breadth_divergence(price, adln)
    assert d["state"] == "bullish_div"
    assert d["length"] >= 1


def test_divergence_building_with_too_little_history():
    assert bm.breadth_divergence([1, 2], [1, 2])["state"] == "none"


def test_near_high_big_breadth_divergence():
    # 2026-09-21..10-02 shape: SPX flat/near highs, A-D line bleeding for weeks
    price = [7626, 7657, 7635, 7659, 7661, 7711, 7693, 7670, 7618, 7652, 7732, 7702,
             7660, 7660, 7578, 7643, 7609, 7574, 7540, 7626, 7617, 7735, 7734, 7678,
             7743, 7743.4, 7684, 7671, 7652, 7666]
    adln = [-51, 58, -36, 29, -168, -213, -436, -619, -518, -340, -493, -702, -1009,
            -1174, -1002, -938, -1126, -1309, -1225, -1415, -1334, -1340, -1500, -1650,
            -1493, -1665, -1767, -2038, -1947, -1862]
    d = bm.breadth_divergence(price, adln)
    assert d["state"] == "bearish_div"
    assert d["length"] >= 1
    assert "near_high" in d["detail"]


def test_near_high_ignores_broad_pullback():
    # price and A-D peak together then fall together → no gap, no flag
    price = [100 + i for i in range(20)] + [118, 117, 116]
    adln = [i * 100 for i in range(20)] + [1200, 600, 0]
    assert bm.breadth_divergence(price, adln)["state"] != "bearish_div"
