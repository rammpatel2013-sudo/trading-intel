"""Tests for the realized-volatility helpers."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trading_intel.prices.realized_vol import (
    add_realized_vol,
    log_returns,
    realized_vol,
    rv_rolloff_projection,
)


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


def _prices_from_returns(returns: list[float]) -> pd.Series:
    """Reconstruct a close series whose log-returns equal ``returns``."""
    close = [100.0]
    for r in returns:
        close.append(close[-1] * float(np.exp(r)))
    return pd.Series(close)


def test_rv_rolloff_shape_and_offsets():
    close = _prices_from_returns([0.005] * 40)
    proj = rv_rolloff_projection(close, window=21, horizon=10)
    assert list(proj.columns) == ["session_offset", "projected_rv", "dropped_return"]
    assert proj["session_offset"].tolist() == list(range(11))
    assert pd.isna(proj.loc[0, "dropped_return"])


def test_rv_rolloff_offset0_matches_trailing_rv():
    # Offset 0 must equal the plain trailing-window realized vol.
    rng = np.random.default_rng(7)
    close = _prices_from_returns(list(rng.normal(0, 0.01, 60)))
    proj = rv_rolloff_projection(close, window=21, horizon=5)
    direct = realized_vol(close, 21).iloc[-1]
    assert proj.loc[0, "projected_rv"] == pytest.approx(direct, rel=1e-9)


def test_rv_rolloff_big_day_ages_out_drops_vol():
    # One big move as the OLDEST return in the window; calm (zero) tape ahead.
    # It should age out at offset 1 and collapse projected RV to ~0.
    returns = [0.08] + [0.0] * 20  # 21 returns, big one is oldest
    close = _prices_from_returns(returns)
    proj = rv_rolloff_projection(close, window=21, horizon=3, future_return=0.0)
    assert proj.loc[0, "projected_rv"] > 0.0
    assert proj.loc[1, "projected_rv"] == pytest.approx(0.0, abs=1e-12)
    assert proj.loc[1, "dropped_return"] == pytest.approx(0.08)
    # Monotonic non-increasing under a zero-return roll-off.
    vals = proj["projected_rv"].to_numpy()
    assert np.all(np.diff(vals) <= 1e-12)
