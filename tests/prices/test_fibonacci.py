"""Tests for the Fibonacci level helpers."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from trading_intel.prices.fibonacci import fib_levels, swing_high_low


def _prices() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": [date(2026, 5, 18), date(2026, 5, 19), date(2026, 5, 20),
                     date(2026, 5, 21), date(2026, 5, 22)],
            "high": [104.0, 110.0, 108.0, 106.0, 107.0],
            "low": [101.0, 105.0, 100.0, 103.0, 104.0],
            "close": [103.0, 109.0, 101.0, 105.0, 106.0],
        }
    )


def test_swing_high_low_uses_high_low():
    hi, lo, hi_d, lo_d = swing_high_low(_prices(), lookback=10)
    assert hi == pytest.approx(110.0)
    assert lo == pytest.approx(100.0)
    assert hi_d == date(2026, 5, 19)
    assert lo_d == date(2026, 5, 20)


def test_fib_levels_values():
    fib = fib_levels(_prices(), lookback=10)
    assert fib is not None
    # span = 10 ; retracements from high (110) down to low (100)
    assert fib.levels["0.0%"] == pytest.approx(110.0)
    assert fib.levels["50.0%"] == pytest.approx(105.0)
    assert fib.levels["61.8%"] == pytest.approx(110.0 - 0.618 * 10)
    assert fib.levels["100.0%"] == pytest.approx(100.0)
    # downside extension below the low
    assert fib.levels["161.8%"] == pytest.approx(110.0 - 1.618 * 10)


def test_fib_levels_degenerate_and_empty():
    flat = pd.DataFrame({"date": [date(2026, 5, 22)], "high": [100.0], "low": [100.0],
                         "close": [100.0]})
    assert fib_levels(flat) is None
    assert fib_levels(pd.DataFrame()) is None
    assert swing_high_low(pd.DataFrame()) is None


def test_fib_falls_back_to_close_without_high_low():
    df = pd.DataFrame({"date": [date(2026, 5, 21), date(2026, 5, 22)], "close": [100.0, 120.0]})
    swing = swing_high_low(df, lookback=10)
    assert swing is not None
    assert swing[0] == pytest.approx(120.0)
    assert swing[1] == pytest.approx(100.0)
