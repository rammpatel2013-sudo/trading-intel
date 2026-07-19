"""Tests for the pure swing feature math (RV + 25d skew)."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from trading_intel.swing.features import iv_rv_ratio, realized_vol, skew_25d

_REF = date(2026, 7, 16)


def test_realized_vol_zero_for_constant_log_returns():
    closes = 100.0 * 1.01 ** np.arange(25)  # constant daily ratio -> zero vol
    assert realized_vol(closes) == pytest.approx(0.0, abs=1e-9)


def test_realized_vol_positive_for_noisy_series():
    rng = np.random.default_rng(0)
    closes = 100 * np.cumprod(1 + rng.normal(0, 0.02, 60))
    rv = realized_vol(closes)
    assert rv is not None and rv > 0


def test_realized_vol_none_when_too_short():
    assert realized_vol(np.arange(5.0), window=20) is None


def _chain() -> pd.DataFrame:
    exp_near = pd.Timestamp(_REF) + pd.Timedelta(days=40)  # in 25-60 DTE window
    exp_far = pd.Timestamp(_REF) + pd.Timedelta(days=400)  # excluded
    return pd.DataFrame(
        [
            {"opt_kind": "C", "delta": 0.25, "iv": 0.30, "expiration": exp_near},
            {"opt_kind": "C", "delta": 0.50, "iv": 0.28, "expiration": exp_near},
            {"opt_kind": "P", "delta": -0.25, "iv": 0.35, "expiration": exp_near},
            {"opt_kind": "P", "delta": -0.50, "iv": 0.33, "expiration": exp_near},
            {"opt_kind": "P", "delta": -0.25, "iv": 0.90, "expiration": exp_far},
        ]
    )


def test_skew_25d_put_over_call_on_near_expiry():
    # 25d put IV (0.35) - 25d call IV (0.30) = +0.05, far expiry ignored
    assert skew_25d(_chain(), ref=_REF) == pytest.approx(0.05)


def test_skew_25d_none_without_a_wing_in_window():
    calls_only = _chain()
    calls_only = calls_only[calls_only["opt_kind"] == "C"]
    assert skew_25d(calls_only, ref=_REF) is None


def test_skew_25d_none_on_empty():
    assert skew_25d(pd.DataFrame(), ref=_REF) is None


def test_iv_rv_ratio_guards():
    assert iv_rv_ratio(0.30, 0.20) == pytest.approx(1.5)
    assert iv_rv_ratio(None, 0.2) is None
    assert iv_rv_ratio(0.3, 0) is None
