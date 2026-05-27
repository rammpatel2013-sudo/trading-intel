"""Tests for the shared Black-Scholes simulation helpers (ADR-002)."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from trading_intel.greeks.black_scholes import bs_gamma, dollar_gamma, years_to_expiry


def test_bs_gamma_atm_known_value():
    # ATM, sigma=0.2, t=1y, r=0  -> gamma = pdf(0.1)/(100*0.2*1) ~= 0.019851
    g = bs_gamma(100.0, np.array([100.0]), np.array([0.2]), np.array([1.0]), 0.0)[0]
    assert g == pytest.approx(0.019851, abs=1e-5)


def test_dollar_gamma_sign_and_scale():
    # call (+) vs put (-) at the same strike: equal magnitude, opposite sign.
    args = (100.0, np.array([100.0]), np.array([0.2]), np.array([1.0]),
            np.array([1000.0]))
    call = dollar_gamma(*args, np.array([1.0]))[0]
    put = dollar_gamma(*args, np.array([-1.0]))[0]
    assert call > 0 and put < 0
    assert call == pytest.approx(-put)
    # magnitude = gamma * oi * 100 * S^2 * 0.01
    g = bs_gamma(100.0, np.array([100.0]), np.array([0.2]), np.array([1.0]))[0]
    assert call == pytest.approx(g * 1000.0 * 100.0 * 100.0**2 * 0.01)


def test_years_to_expiry_formats():
    ref = date(2026, 5, 26)
    # datetime column: ~3 days
    dt = years_to_expiry(pd.to_datetime(pd.Series([date(2026, 5, 29)])), ref)
    assert dt[0] == pytest.approx(3.0 / 365.0, abs=1e-3)
    # epoch-day integers (Convex native): days since the Unix epoch
    epoch_day = (date(2026, 5, 29) - date(1970, 1, 1)).days
    epoch = years_to_expiry(pd.Series([epoch_day]), ref)
    assert epoch[0] == pytest.approx(3.0 / 365.0, abs=2e-3)
    # plain days-to-expiry
    dte = years_to_expiry(pd.Series([30]), ref)
    assert dte[0] == pytest.approx(30.0 / 365.0, abs=1e-6)
