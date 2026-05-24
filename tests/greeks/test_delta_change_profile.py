"""Tests for the centered-at-50d delta change profile (pure)."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from trading_intel.greeks.surface_changes import delta_change_profile

_GRID = [0.05, 0.075, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.475, 0.50]
_EXP = date(2026, 6, 21)


def _chain(put_iv: float, call_iv: float) -> pd.DataFrame:
    rows = []
    for n, d in enumerate(_GRID):
        rows.append((5000 - 100 * n, "P", put_iv, -d, _EXP))
        rows.append((5000 + 100 * n, "C", call_iv, d, _EXP))
    df = pd.DataFrame(rows, columns=["strike", "cp", "iv", "delta", "expiry"])
    df["opt_kind"] = df["cp"]
    df["expiration"] = pd.to_datetime(df["expiry"])
    return df


def test_centered_at_atm_and_change_values():
    prof = delta_change_profile(_chain(0.20, 0.20), _chain(0.21, 0.205))
    atm = prof[prof["side"] == "atm"]
    assert not atm.empty and atm["order"].iloc[0] == 11  # 50d ATM at the centre
    assert prof["order"].min() == 0 and prof["order"].max() == 22  # 12 puts + ATM + 11 calls
    assert prof[prof["side"] == "put"]["d_iv_pts"].mean() == pytest.approx(1.0, abs=0.05)
    assert prof[prof["side"] == "call"]["d_iv_pts"].mean() == pytest.approx(0.5, abs=0.05)
