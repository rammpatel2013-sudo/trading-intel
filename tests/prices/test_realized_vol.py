"""Tests for the realized-volatility helpers."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trading_intel.prices.realized_vol import add_realized_vol, log_returns, realized_vol


def test_log_returns_first_is_nan_and_values():
    close = pd.Series([100.0, 110.0, 99.0])
    lr = log_returns(close)
    assert np.isnan(lr.iloc[0])
    assert lr.iloc[1] == pytest.approx(np.log(110 / 100))
    assert lr.iloc[2] == pytest.approx(np.log(99 / 110))


def test_realized_vol_matches_independent_numpy():
    rng = np.random.default_rng(42)
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 100))))
    out = realized_vol(close, 20)
    # Independent recompute of the last window.
    rets = np.log(close / close.shift(1)).dropna().to_numpy()
    expected = rets[-20:].std(ddof=1) * np.sqrt(252)
    assert out.iloc[-1] == pytest.approx(expected)
    assert out.iloc[:20].isna().all()  # needs 20 returns to prime


def test_realized_vol_zero_for_constant_price():
    close = pd.Series([50.0] * 30)
    assert realized_vol(close, 10).dropna().eq(0.0).all()


def test_add_realized_vol_adds_columns():
    close = pd.Series(100 * np.exp(np.cumsum(np.full(70, 0.001))))
    df = pd.DataFrame({"close": close})
    out = add_realized_vol(df, windows=(20, 60))
    assert "rv20" in out.columns and "rv60" in out.columns
    assert out["rv20"].notna().any()
    assert out["rv20"].iloc[:20].isna().all()


def test_add_realized_vol_missing_close_is_safe():
    out = add_realized_vol(pd.DataFrame({"x": [1, 2, 3]}), windows=(20,))
    assert "rv20" in out.columns
