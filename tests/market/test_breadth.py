"""Market-breadth compute — pure closes-in/numbers-out, no fetch."""

from __future__ import annotations

from trading_intel.market.breadth import above_ma, advance_decline, compute_breadth


def test_above_ma_directions_and_history_guard():
    assert above_ma(list(range(1, 101)), 50) is True  # rising → above its MA
    assert above_ma(list(range(100, 0, -1)), 50) is False  # falling → below
    assert above_ma([1, 2, 3], 200) is None  # not enough history


def test_compute_breadth_and_advance_decline():
    rising = list(range(1, 260))
    falling = list(range(300, 41, -1))
    data = {"A": rising, "B": rising, "C": rising, "D": falling}  # 3 up, 1 down
    b = compute_breadth(data, sessions=5)
    assert b.pct_above_50 == 75
    assert b.pct_above_200 == 75
    assert b.n == 4
    assert (b.advancers, b.decliners) == (3, 1)
    assert len(b.trend_50) == 5 and all(v == 75 for v in b.trend_50)


def test_advance_decline_standalone():
    assert advance_decline({"A": [10, 11], "B": [10, 9], "C": [5, 5]}) == (1, 1)
