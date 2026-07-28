"""Tests for the scaled (partial-exit) R engine — the Yamco T1/T2/T3 method."""

from __future__ import annotations

from datetime import date

import pytest

from trading_intel.backtest.em_break import (
    ScaleLeg,
    bars_from_rows,
    legs_from_r_multiples,
    scaled_exit_r,
)


def test_legs_from_r_multiples():
    legs = legs_from_r_multiples(100, 95, [1, 2, 3], [0.3, 0.3, 0.4])
    assert [leg.target for leg in legs] == [105, 110, 115]
    assert [leg.fraction for leg in legs] == [0.3, 0.3, 0.4]


def test_all_targets_hit_blended_r():
    # 0.3@1R + 0.3@2R + 0.4@3R = 0.3 + 0.6 + 1.2 = 2.1R
    legs = legs_from_r_multiples(100, 95, [1, 2, 3], [0.3, 0.3, 0.4])
    path = bars_from_rows([(date(2026, 7, 2), 120, 99, 118)])
    oc = scaled_exit_r(100, 95, legs, path)
    assert oc.result == "target"
    assert oc.legs_hit == 3
    assert oc.blended_r == pytest.approx(2.1)
    assert oc.closed_fraction == pytest.approx(1.0)


def test_full_stop_no_targets():
    legs = legs_from_r_multiples(100, 95, [1, 2, 3], [0.3, 0.3, 0.4])
    path = bars_from_rows([(date(2026, 7, 2), 101, 94, 96)])
    oc = scaled_exit_r(100, 95, legs, path)
    assert oc.result == "stop"
    assert oc.blended_r == pytest.approx(-1.0)
    assert oc.legs_hit == 0


def test_t1_then_breakeven_stop_protects_green():
    # T1 (0.5 @ +1R) then a pullback to the ratcheted breakeven stop: 0.5*1R + 0.5*0R.
    legs = [ScaleLeg(105, 0.5), ScaleLeg(115, 0.5)]
    path = bars_from_rows(
        [
            (date(2026, 7, 2), 106, 99, 104),  # hits T1 @105, stop -> BE 100
            (date(2026, 7, 3), 101, 100, 100),  # touches BE stop
        ]
    )
    oc = scaled_exit_r(100, 95, legs, path)
    assert oc.result == "mixed"
    assert oc.legs_hit == 1
    assert oc.blended_r == pytest.approx(0.5)
    assert oc.days_held == 2


def test_open_remainder_marked_to_last_close():
    # T1 (0.5 @ +1R) booked; remaining 0.5 rides to last close 107 = +1.4R on that half.
    legs = [ScaleLeg(105, 0.5)]
    path = bars_from_rows(
        [
            (date(2026, 7, 2), 106, 99, 104),
            (date(2026, 7, 3), 108, 101, 107),
        ]
    )
    oc = scaled_exit_r(100, 95, legs, path)
    assert oc.result == "mixed"
    assert oc.blended_r == pytest.approx(0.5 * 1.0 + 0.5 * 1.4)


def test_invalid_geometry_returns_none():
    legs = [ScaleLeg(105, 0.5)]
    good = bars_from_rows([(date(2026, 7, 2), 106, 99, 104)])
    assert scaled_exit_r(100, 100, legs, good) is None  # stop == entry
    assert scaled_exit_r(100, 95, [ScaleLeg(95, 0.5)], good) is None  # target below entry
    assert scaled_exit_r(100, 95, [ScaleLeg(105, 0.8), ScaleLeg(110, 0.5)], good) is None  # frac > 1
    assert scaled_exit_r(100, 95, legs, []) is None  # empty path
