"""Unit tests for trading_intel.backtest.metrics — pure NumPy, no DB."""

from __future__ import annotations

import math

import numpy as np
import pytest

from trading_intel.backtest.metrics import (
    MIN_SAMPLES,
    ReturnStats,
    lift_vs_baseline,
    summarize,
)


def test_summarize_empty_returns_zero_n_and_nones():
    s = summarize(np.asarray([], dtype=float))
    assert s.n == 0
    assert s.mean is None and s.std is None and s.ir is None
    assert s.hit_rate is None and s.p05 is None and s.p95 is None


def test_summarize_below_min_samples_is_none_filled():
    arr = np.asarray([0.01, 0.02, -0.01], dtype=float)
    assert arr.size < MIN_SAMPLES
    s = summarize(arr)
    assert s.n == arr.size
    assert s.mean is None and s.median is None
    assert s.std is None and s.ir is None


def test_summarize_known_answer():
    # 6 values, mean = 0.01, sd ≈ 0.012649, ir ≈ 0.7906, hit_rate = 4/6
    arr = np.asarray([0.0, 0.01, 0.02, 0.03, -0.01, 0.01], dtype=float)
    s = summarize(arr)
    assert s.n == 6
    assert s.mean == pytest.approx(arr.mean(), abs=1e-12)
    assert s.median == pytest.approx(float(np.median(arr)), abs=1e-12)
    assert s.std == pytest.approx(float(arr.std(ddof=1)), abs=1e-12)
    assert s.ir is not None
    assert s.ir == pytest.approx(arr.mean() / arr.std(ddof=1), abs=1e-12)
    assert s.hit_rate == pytest.approx(4 / 6, abs=1e-12)


def test_summarize_drops_non_finite_values():
    arr = np.asarray([0.01, np.nan, 0.02, np.inf, -0.01, 0.03, 0.0], dtype=float)
    s = summarize(arr)
    # nan and inf both dropped; 5 finite values remain.
    assert s.n == 5
    assert s.mean is not None and math.isfinite(s.mean)


def test_summarize_zero_std_ir_is_none():
    arr = np.asarray([0.01, 0.01, 0.01, 0.01, 0.01], dtype=float)
    s = summarize(arr)
    assert s.std == 0.0
    assert s.ir is None  # divide-by-zero guard, not inf


def test_lift_vs_baseline_simple():
    base = ReturnStats(
        n=10,
        mean=0.001,
        median=0.0,
        std=0.01,
        ir=0.1,
        hit_rate=0.5,
        p05=-0.02,
        p25=-0.005,
        p75=0.005,
        p95=0.02,
        min=-0.03,
        max=0.03,
    )
    state = ReturnStats(
        n=10,
        mean=0.005,
        median=0.004,
        std=0.012,
        ir=0.4,
        hit_rate=0.7,
        p05=-0.01,
        p25=0.0,
        p75=0.01,
        p95=0.025,
        min=-0.02,
        max=0.04,
    )
    assert lift_vs_baseline(state, base) == pytest.approx(0.004, abs=1e-12)


def test_lift_vs_baseline_none_when_either_missing():
    base = ReturnStats(
        n=0,
        mean=None,
        median=None,
        std=None,
        ir=None,
        hit_rate=None,
        p05=None,
        p25=None,
        p75=None,
        p95=None,
        min=None,
        max=None,
    )
    state = ReturnStats(
        n=10,
        mean=0.005,
        median=0.004,
        std=0.012,
        ir=0.4,
        hit_rate=0.7,
        p05=-0.01,
        p25=0.0,
        p75=0.01,
        p95=0.025,
        min=-0.02,
        max=0.04,
    )
    assert lift_vs_baseline(state, base) is None
    assert lift_vs_baseline(base, state) is None
