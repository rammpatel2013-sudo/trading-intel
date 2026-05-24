"""Tests for the market-bias synthesis (pure)."""

from __future__ import annotations

from trading_intel.dashboard.market_timing import market_bias


def test_transitional_takes_priority():
    # Even with otherwise-calm inputs, a flip => Transitional.
    assert market_bias("transitional", "low", "contango").label == "Transitional"


def test_risk_off_from_negative_gamma():
    assert market_bias("negative", "mid", "contango").label == "Risk-off / trending"


def test_risk_off_from_backwardation_even_if_positive_gamma():
    assert market_bias("positive", "low", "backwardation").label == "Risk-off / trending"


def test_risk_off_from_high_vix():
    assert market_bias("positive", "high", "contango").label == "Risk-off / trending"


def test_calm_regime():
    assert market_bias("positive", "low", "contango").label == "Calm / range-bound"
    assert market_bias("positive", "low", None).label == "Calm / range-bound"


def test_mixed_when_unaligned():
    # Positive gamma but mid VIX -> not calm, not risk-off -> Mixed.
    assert market_bias("positive", "mid", "contango").label == "Mixed"
    assert market_bias(None, None, None).label == "Mixed"
