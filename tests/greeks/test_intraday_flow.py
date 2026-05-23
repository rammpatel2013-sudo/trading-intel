"""Tests for the volume-weighted intraday 0DTE/1DTE exposure helpers."""

from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from trading_intel.errors import ComputationError
from trading_intel.greeks.intraday_flow import (
    dte_days,
    filter_0dte_1dte,
    interval_volume,
    is_market_hours,
    volume_weighted_by_strike,
    volume_weighted_exposures,
)

REF = date(2026, 5, 22)


def _chain() -> pd.DataFrame:
    return pd.DataFrame(
        [
            # 0DTE
            {"expiration": pd.Timestamp("2026-05-22"), "opt_kind": "call", "strike": 100.0,
             "gamma": 0.05, "delta": 0.5, "vanna": 0.1, "charm": 0.02, "iv": 0.20,
             "volume": 1000},
            {"expiration": pd.Timestamp("2026-05-22"), "opt_kind": "put", "strike": 100.0,
             "gamma": 0.04, "delta": -0.5, "vanna": 0.1, "charm": 0.02, "iv": 0.20,
             "volume": 500},
            # 1DTE
            {"expiration": pd.Timestamp("2026-05-23"), "opt_kind": "call", "strike": 105.0,
             "gamma": 0.03, "delta": 0.3, "vanna": 0.05, "charm": 0.01, "iv": 0.25,
             "volume": 200},
            # 5DTE — should be filtered out
            {"expiration": pd.Timestamp("2026-05-27"), "opt_kind": "call", "strike": 110.0,
             "gamma": 0.02, "delta": 0.2, "vanna": 0.04, "charm": 0.01, "iv": 0.30,
             "volume": 900},
        ]
    )


def test_is_market_hours():
    assert is_market_hours(datetime(2026, 5, 22, 10, 0))      # Fri 10:00
    assert is_market_hours(datetime(2026, 5, 22, 9, 30))      # open edge
    assert not is_market_hours(datetime(2026, 5, 22, 9, 29))  # pre-open
    assert not is_market_hours(datetime(2026, 5, 22, 16, 1))  # post-close
    assert not is_market_hours(datetime(2026, 5, 23, 11, 0))  # Saturday


def test_dte_days():
    out = dte_days(pd.Series([pd.Timestamp("2026-05-22"), pd.Timestamp("2026-05-24")]), REF)
    assert list(out) == [0, 2]


def test_filter_0dte_1dte_keeps_only_near_tenor():
    out = filter_0dte_1dte(_chain(), ref=REF, max_dte=1)
    assert set(out["dte"]) == {0, 1}
    assert 110.0 not in set(out["strike"])  # 5DTE dropped


def test_volume_weighted_by_strike_signs_and_formulas():
    chain = filter_0dte_1dte(_chain(), ref=REF, max_dte=1)
    out = volume_weighted_by_strike(chain, spot=100.0).set_index("strike")
    # strike 100: gamma_vol = +0.05*1000 (call) - 0.04*500 (put) = 50 - 20 = 30
    assert out.loc[100.0, "gamma_vol"] == pytest.approx(30.0)
    # delta_vol = 0.5*1000 + (-0.5)*500 = 500 - 250 = 250
    assert out.loc[100.0, "delta_vol"] == pytest.approx(250.0)
    # vanna_vol strike100 = (0.1*1000 + 0.1*500) * spot(100) * iv(0.20)
    assert out.loc[100.0, "vanna_vol"] == pytest.approx((0.1 * 1000 + 0.1 * 500) * 100 * 0.20)
    # charm_vol strike105 = 0.01*200 * 100 * 365
    assert out.loc[105.0, "charm_vol"] == pytest.approx(0.01 * 200 * 100 * 365)


def test_volume_weighted_exposures_totals():
    chain = filter_0dte_1dte(_chain(), ref=REF, max_dte=1)
    agg = volume_weighted_exposures(chain, spot=100.0)
    assert agg["gamma_vol"] == pytest.approx(30.0 + 0.03 * 200)  # +strike105 call gamma
    assert agg["total_volume"] == pytest.approx(1000 + 500 + 200)


def test_volume_weighted_empty_and_bad_spot():
    assert volume_weighted_exposures(pd.DataFrame(), 100.0) == {}
    assert volume_weighted_by_strike(pd.DataFrame(), 100.0).empty
    with pytest.raises(ComputationError):
        volume_weighted_by_strike(_chain(), spot=0.0)


def test_volume_weighted_bad_opt_kind_raises():
    bad = pd.DataFrame(
        [{"expiration": pd.Timestamp("2026-05-22"), "opt_kind": "X", "strike": 100.0,
          "gamma": 0.05, "delta": 0.5, "vanna": 0.1, "charm": 0.02, "iv": 0.20, "volume": 10}]
    )
    with pytest.raises(ComputationError):
        volume_weighted_by_strike(bad, spot=100.0)


def test_interval_volume_diffs_against_prior():
    curr = pd.DataFrame(
        [
            {"expiry": pd.Timestamp("2026-05-22"), "strike": 100.0, "opt_kind": "call",
             "volume": 1200},
            {"expiry": pd.Timestamp("2026-05-22"), "strike": 105.0, "opt_kind": "call",
             "volume": 50},  # new strike, no prior
        ]
    )
    prev = pd.DataFrame(
        [
            {"expiry": pd.Timestamp("2026-05-22"), "strike": 100.0, "opt_kind": "call",
             "volume": 1000},
        ]
    )
    out = interval_volume(curr, prev).set_index("strike")
    assert out.loc[100.0, "volume_interval"] == pytest.approx(200.0)  # 1200 - 1000
    assert np.isnan(out.loc[105.0, "volume_interval"])  # no prior match


def test_interval_volume_no_prior_all_nan():
    curr = pd.DataFrame(
        [{"expiry": pd.Timestamp("2026-05-22"), "strike": 100.0, "opt_kind": "call",
          "volume": 100}]
    )
    out = interval_volume(curr, None)
    assert out["volume_interval"].isna().all()
