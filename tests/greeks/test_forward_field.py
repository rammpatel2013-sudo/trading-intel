"""Tests for the forward gamma/charm field (ADR-002 simulation)."""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from trading_intel.greeks.black_scholes import bs_charm
from trading_intel.greeks.forward_field import forward_field, session_close_grid

_EXP = pd.Timestamp("2026-05-26")  # 0DTE for the test "today"


def _chain() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "opt_kind": ["call", "call", "call"],
            "strike": [7350, 7400, 7450],
            "iv": [0.18, 0.18, 0.18],
            "oi": [5000, 5000, 5000],
            "expiration": [_EXP, _EXP, _EXP],
        }
    )


def test_bs_charm_atm_known_value():
    # ATM, sigma=0.2, T=1y, r=0  -> ~ -0.01985
    c = bs_charm(100.0, np.array([100.0]), np.array([0.2]), np.array([1.0]), 0.0)[0]
    assert c == pytest.approx(-0.01985, abs=1e-3)


def test_gamma_field_sharpens_into_close():
    times = [datetime(2026, 5, 26, 10, 0), datetime(2026, 5, 26, 15, 55)]
    field = forward_field(_chain(), 7400.0, greek="gamma", times=times)
    assert list(field.index) == [7350.0, 7400.0, 7450.0]
    assert list(field.columns) == times
    # long-call dealer gamma is positive
    assert (field.loc[7400.0] > 0).all()
    # ATM gamma intensifies toward the close; the wings decay (T -> 0)
    assert abs(field.loc[7400.0].iloc[-1]) > abs(field.loc[7400.0].iloc[0])
    assert abs(field.loc[7350.0].iloc[-1]) < abs(field.loc[7350.0].iloc[0])


def test_charm_field_finite_and_shaped():
    times = [datetime(2026, 5, 26, 11, 0), datetime(2026, 5, 26, 15, 0)]
    field = forward_field(_chain(), 7400.0, greek="charm", times=times)
    assert field.shape == (3, 2)
    assert np.isfinite(field.to_numpy()).all()


def test_forward_field_empty_and_invalid():
    times = [datetime(2026, 5, 26, 11, 0)]
    assert forward_field(pd.DataFrame(), 7400.0, greek="gamma", times=times).empty
    assert forward_field(_chain(), 0.0, greek="gamma", times=times).empty
    assert forward_field(_chain(), 7400.0, greek="gamma", times=[]).empty
    with pytest.raises(ValueError, match="unknown greek"):
        forward_field(_chain(), 7400.0, greek="vega", times=times)


def test_session_close_grid():
    grid = session_close_grid(datetime(2026, 5, 26, 15, 42), step_minutes=10)
    assert grid[-1] == datetime(2026, 5, 26, 16, 0)
    assert all(grid[i] < grid[i + 1] for i in range(len(grid) - 1))
    # already past the close -> single timestamp
    after = session_close_grid(datetime(2026, 5, 26, 16, 30))
    assert len(after) == 1
