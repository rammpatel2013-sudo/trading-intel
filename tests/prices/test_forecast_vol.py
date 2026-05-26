"""Tests for the forward realized-vol forecasters (HAR-RV + EWMA)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trading_intel.prices.forecast_vol import (
    daily_variance,
    dte_to_trading_days,
    ewma_variance,
    fit_har,
    forecast_ewma_rv,
    forecast_vol,
    forward_mean_variance,
    har_components,
)

_TRADING_DAYS = 252


def _gbm(n: int, sigma_d: float, *, seed: int = 7) -> pd.Series:
    """Constant-vol geometric Brownian close path."""
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.0, sigma_d, n)
    return pd.Series(100.0 * np.exp(np.cumsum(rets)))


# ── horizon mapping ────────────────────────────────────────────────────


def test_dte_to_trading_days_mapping():
    assert dte_to_trading_days(30) == 21
    assert dte_to_trading_days(60) == 41
    assert dte_to_trading_days(0) == 1  # floored at 1


# ── daily variance + HAR components ────────────────────────────────────


def test_daily_variance_is_squared_log_return():
    close = pd.Series([100.0, 110.0, 99.0])
    v = daily_variance(close)
    assert np.isnan(v.iloc[0])
    assert v.iloc[1] == pytest.approx(np.log(110 / 100) ** 2)


def test_har_components_window_priming():
    v = daily_variance(_gbm(60, 0.01))
    comp = har_components(v)
    # weekly primes at index 5, monthly at index 22 (index 0 variance is NaN).
    assert np.isnan(comp["vw"].iloc[4])
    assert comp["vw"].notna().iloc[5]
    assert np.isnan(comp["vm"].iloc[21])
    assert comp["vm"].notna().iloc[22]


def test_forward_mean_variance_known_values():
    v = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    fwd = forward_mean_variance(v, 2)
    assert fwd.iloc[0] == pytest.approx(2.5)  # mean(2,3)
    assert fwd.iloc[3] == pytest.approx(5.5)  # mean(5,6)
    assert np.isnan(fwd.iloc[4]) and np.isnan(fwd.iloc[5])  # no future left


# ── HAR fit / forecast ─────────────────────────────────────────────────


def test_fit_har_returns_none_when_too_short():
    assert fit_har(_gbm(40, 0.01), 21, min_obs=60) is None


def test_fit_har_recovers_constant_vol_level():
    sigma_d = 0.012
    fit = fit_har(_gbm(800, sigma_d), 21)
    assert fit is not None
    assert fit.n_obs > 600
    assert 0.0 <= fit.r2 <= 1.0  # in-sample OLS R2 is bounded
    annual = sigma_d * np.sqrt(_TRADING_DAYS)  # ~0.19
    # HAR on squared daily returns is noisy; expect the level, loosely.
    assert fit.forecast_rv == pytest.approx(annual, rel=0.35)


def test_fit_har_forecast_nonnegative_on_constant_price():
    fit = fit_har(pd.Series([50.0] * 200), 21)
    assert fit is not None
    assert fit.forecast_rv == pytest.approx(0.0, abs=1e-9)


# ── EWMA ───────────────────────────────────────────────────────────────


def test_ewma_variance_matches_manual_recursion():
    close = pd.Series([100.0, 101.0, 99.0, 100.0, 102.0])
    lam = 0.94
    rets = np.log(close / close.shift(1)).dropna().to_numpy()
    sq = rets**2
    expected = np.empty_like(sq)
    expected[0] = sq[0]
    for t in range(1, len(sq)):
        expected[t] = lam * expected[t - 1] + (1 - lam) * sq[t]
    out = ewma_variance(close, lam=lam)
    assert out.to_numpy() == pytest.approx(expected)


def test_forecast_ewma_rv_none_without_returns():
    assert forecast_ewma_rv(pd.Series([100.0])) is None


def test_forecast_ewma_rv_annualizes():
    rv = forecast_ewma_rv(_gbm(300, 0.01))
    assert rv is not None and 0.05 < rv < 0.35


# ── combined ───────────────────────────────────────────────────────────


def test_forecast_vol_both_horizons_share_ewma():
    out = forecast_vol(_gbm(800, 0.011), horizons=(30, 60))
    assert set(out) == {30, 60}
    assert out[30].horizon_trading_days == 21
    assert out[60].horizon_trading_days == 41
    assert out[30].har_rv is not None and out[60].har_rv is not None
    # EWMA is horizon-independent (flat forward) → identical across horizons.
    assert out[30].ewma_rv == out[60].ewma_rv


def test_forecast_vol_short_series_har_none_ewma_present():
    out = forecast_vol(_gbm(45, 0.01), horizons=(30,), min_obs=60)
    assert out[30].har_rv is None
    assert out[30].n_obs == 0
    assert out[30].ewma_rv is not None  # EWMA still available
