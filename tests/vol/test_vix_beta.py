"""Tests for the rolling VIX-beta estimator (``vol.vix_beta``)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trading_intel.vol.vix_beta import abnormal_rr_change, vix_beta


def _series(values: list[float], *, start: str = "2026-01-01") -> pd.Series:
    idx = pd.date_range(start=start, periods=len(values), freq="B")
    return pd.Series(values, index=idx, dtype=float)


def test_vix_beta_recovers_known_slope():
    rng = np.random.default_rng(42)
    n = 200
    d_vix = rng.normal(0.0, 1.0, size=n)
    d_iv = 1.7 * d_vix + rng.normal(0.0, 0.05, size=n)
    # Build cumulative-level series from the differences.
    vix = _series(np.cumsum(d_vix).tolist())
    iv = _series(np.cumsum(d_iv).tolist())
    beta = vix_beta(iv, vix, window=120, min_obs=60)
    assert beta is not None
    assert beta == pytest.approx(1.7, rel=0.05)


def test_vix_beta_cold_under_min_obs():
    iv = _series([0.20, 0.21, 0.22])
    vix = _series([15.0, 15.5, 16.0])
    assert vix_beta(iv, vix, min_obs=40) is None


def test_vix_beta_none_on_zero_variance_vix():
    iv = _series(list(np.linspace(0.20, 0.40, 80)))
    vix = _series([15.0] * 80)  # constant -> variance 0 after differencing
    assert vix_beta(iv, vix, min_obs=40) is None


def test_vix_beta_handles_unaligned_indices():
    # Two series with overlapping but offset dates — alignment should win.
    a = _series(list(np.linspace(0.20, 0.40, 100)), start="2026-01-01")
    b = _series(list(np.linspace(10.0, 30.0, 100)), start="2026-01-15")  # shifted
    beta = vix_beta(a, b, window=60, min_obs=20)
    # Both series are deterministic linear ramps; once aligned the slope is finite.
    assert beta is not None and np.isfinite(beta)


def test_vix_beta_none_when_either_input_empty():
    assert vix_beta(pd.Series(dtype=float), _series([15.0, 16.0])) is None
    assert vix_beta(_series([0.2, 0.21]), pd.Series(dtype=float)) is None


# ── abnormal_rr_change ─────────────────────────────────────────────────


def test_abnormal_rr_change_residual_math():
    # Δrr_name = +0.40, β = 1.5, Δsdex = +0.20 → residual = 0.40 - 0.30 = 0.10
    assert abnormal_rr_change(
        d_rr_name=0.40, d_index_skew=0.20, beta=1.5
    ) == pytest.approx(0.10)


def test_abnormal_rr_change_none_on_any_missing():
    assert abnormal_rr_change(d_rr_name=None, d_index_skew=0.1, beta=1.0) is None
    assert abnormal_rr_change(d_rr_name=0.1, d_index_skew=None, beta=1.0) is None
    assert abnormal_rr_change(d_rr_name=0.1, d_index_skew=0.1, beta=None) is None


def test_abnormal_rr_change_none_on_nan():
    assert abnormal_rr_change(
        d_rr_name=float("nan"), d_index_skew=0.1, beta=1.0
    ) is None
