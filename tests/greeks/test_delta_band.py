"""Tests for the near-the-money delta-band chain filter."""

from __future__ import annotations

import pandas as pd

from trading_intel.greeks.intraday_flow import filter_delta_band


def _chain() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "strike": [90, 95, 100, 105, 110, 100],
            "opt_kind": ["call", "call", "call", "call", "call", "put"],
            "delta": [0.85, 0.65, 0.50, 0.32, 0.12, -0.50],
        }
    )


def test_filter_delta_band_keeps_near_money():
    out = filter_delta_band(_chain(), lo=0.30, hi=0.70)
    # drops |delta| 0.85 (deep ITM) and 0.12 (far OTM); keeps 0.65/0.50/0.32 + the -0.50 put
    assert sorted(out["delta"].abs().round(2).tolist()) == [0.32, 0.50, 0.50, 0.65]


def test_filter_delta_band_empty_and_columnless():
    assert filter_delta_band(pd.DataFrame()).empty
    # no delta column -> returned unchanged (mirrors filter_0dte_1dte)
    assert not filter_delta_band(pd.DataFrame({"strike": [100]})).empty
