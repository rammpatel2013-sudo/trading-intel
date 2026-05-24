"""Tests for the RSI indicator (pure)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trading_intel.prices.technicals import rsi


def test_rsi_all_gains_is_100():
    close = pd.Series(range(1, 41), dtype=float)  # strictly increasing
    r = rsi(close, period=14)
    assert pd.isna(r.iloc[5])  # not enough data early
    assert r.iloc[-1] == pytest.approx(100.0)


def test_rsi_all_losses_is_0():
    close = pd.Series(range(40, 0, -1), dtype=float)  # strictly decreasing
    r = rsi(close, period=14)
    assert r.iloc[-1] == pytest.approx(0.0)


def test_rsi_stays_in_bounds():
    rng = np.random.default_rng(0)
    close = pd.Series(100 + np.cumsum(rng.standard_normal(200)))
    r = rsi(close, period=14).dropna()
    assert not r.empty
    assert (r >= 0).all() and (r <= 100).all()
