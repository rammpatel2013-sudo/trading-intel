"""Tests for the Weinstein stage classifier (pure)."""

from __future__ import annotations

from trading_intel.research.stage import classify, sma


def test_sma_trailing():
    assert sma([1, 2, 3, 4], 2) == [None, 1.5, 2.5, 3.5]


def _ramp(start, step, n):
    return [start + step * i for i in range(n)]


def test_stage2_advancing_above_rising_ma():
    closes = _ramp(10, 0.5, 60)  # steady uptrend -> price above a rising MA
    r = classify(closes, ma_window=30)
    assert r.stage == "Stage 2" and r.above_ma is True and r.ma_slope > 0


def test_stage4_declining_below_falling_ma():
    closes = _ramp(50, -0.5, 60)  # steady downtrend
    r = classify(closes, ma_window=30)
    assert r.stage == "Stage 4" and r.above_ma is False and r.ma_slope < 0


def test_stage1_base_below_flat_ma():
    # long decline then a flat base: last bar below a now-flat/rising MA
    closes = _ramp(50, -1.0, 40) + [11.0] * 20
    r = classify(closes, ma_window=30)
    assert r.stage in ("Stage 1", "Stage 4")  # transition zone
    # after enough flat base the MA stops falling -> Stage 1
    closes2 = _ramp(50, -1.0, 35) + [15.0] * 35
    assert classify(closes2, ma_window=30).stage == "Stage 1"


def test_stage3_top_above_flat_ma():
    closes = _ramp(10, 1.0, 35) + [46.0] * 35  # advance then flat-high -> topping
    assert classify(closes, ma_window=30).stage == "Stage 3"


def test_too_few_bars_returns_none():
    assert classify([1, 2, 3], ma_window=30) is None
