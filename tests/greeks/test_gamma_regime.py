"""Tests for the gamma-regime classifier (pure; SQLite/network not needed)."""

from __future__ import annotations

import pandas as pd
import pytest

from trading_intel.greeks.gamma_regime import (
    classify,
    classify_gamma_regime,
    net_gex,
)


def test_net_gex_signed_sum():
    chain = pd.DataFrame(
        {
            "opt_kind": ["call", "call", "put", "put"],
            "strike": [100, 105, 95, 90],
            "gxoi": [100.0, 200.0, 50.0, 30.0],
        }
    )
    # calls(300) - puts(80) = 220
    assert net_gex(chain) == pytest.approx(220.0)


def test_classify_pure_labels():
    assert classify(220.0, 100.0, None)[0] == "positive"
    assert classify(-50.0, 100.0, None)[0] == "negative"
    assert classify(0.0, 100.0, None)[0] == "transitional"
    # Far from flip -> follows net-GEX sign; distance reported.
    regime, dist = classify(220.0, 100.0, 105.0)
    assert regime == "positive" and dist == pytest.approx(5.0)
    # Within 0.5% of flip -> transitional regardless of sign.
    regime, dist = classify(220.0, 100.0, 100.2)
    assert regime == "transitional" and dist == pytest.approx(0.2)


def test_classify_gamma_regime_positive_when_long_gamma():
    chain = pd.DataFrame(
        {
            "opt_kind": ["call", "call", "call"],
            "strike": [95.0, 100.0, 105.0],
            "gxoi": [100.0, 200.0, 150.0],
            "iv": [0.20, 0.20, 0.20],
            "oi": [1000, 1000, 1000],
            "expiration": [30, 30, 30],  # plain days-to-expiry
        }
    )
    gr = classify_gamma_regime(chain, spot=100.0)
    assert gr.net_gex == pytest.approx(450.0)
    assert gr.flip is None  # all-call (positive) gamma never crosses zero
    assert gr.regime == "positive"
    assert gr.call_wall == pytest.approx(100.0)  # max gxoi at strike 100
    assert "Positive-gamma" in gr.regime_read()
