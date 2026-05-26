"""Tests for term-structure slope, skew-vs-history, and the VEGA/VIX gate."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import numpy as np
import pytest

from trading_intel.vol.term_skew import (
    RegimeGate,
    build_regime_gate,
    classify_skew,
    classify_slope,
    gated_label,
    is_short_vol_label,
    skew_percentile,
    term_slope,
    vix_term_slope,
)

_RICH = "rich (premium-sell candidate, delta-hedge)"
_CHEAP = "cheap (long-vol candidate)"


# ── term slope ─────────────────────────────────────────────────────────


def test_term_slope_value_and_nan_guard():
    assert term_slope(0.20, 0.23) == pytest.approx(0.03)
    assert term_slope(None, 0.2) is None
    assert term_slope(0.2, np.nan) is None


def test_classify_slope_bands():
    assert classify_slope(0.03) == "contango"
    assert classify_slope(-0.03) == "backwardation"
    assert classify_slope(0.001) == "flat"  # within decimal flat band
    assert classify_slope(None) is None


def test_vix_term_slope_uses_points_band():
    slope, label = vix_term_slope(14.07, 22.35)  # live-ish contango
    assert slope == pytest.approx(8.28)
    assert label == "contango"
    # near==far → flat under the 0.5-pt band.
    assert vix_term_slope(20.0, 20.2)[1] == "flat"


# ── 25Δ skew ───────────────────────────────────────────────────────────


def test_classify_skew_states():
    assert "steep" in classify_skew(3.5)
    assert classify_skew(1.5) == "moderate downside skew"
    assert classify_skew(0.0) == "flat"
    assert "inverted" in classify_skew(-2.0)
    assert classify_skew(None) is None


def test_skew_percentile_reuses_percentile_rank():
    hist = list(np.linspace(0.0, 4.0, 40))
    assert skew_percentile(hist, 2.0) == pytest.approx(0.5, abs=0.03)
    assert skew_percentile([1.0, 2.0], 1.5) is None  # cold


# ── regime gate ────────────────────────────────────────────────────────


def test_build_regime_gate_zones():
    assert build_regime_gate(15.0).short_vol_allowed is True  # carry
    assert build_regime_gate(27.0).short_vol_allowed is True  # fragility
    high = build_regime_gate(40.0)
    assert high.zone == "high" and high.short_vol_allowed is False
    assert "gated OFF" in high.note
    unknown = build_regime_gate(None)
    assert unknown.short_vol_allowed is True  # gate inactive, not fabricated
    assert "unavailable" in unknown.note


def test_is_short_vol_label():
    assert is_short_vol_label(_RICH) is True
    assert is_short_vol_label(_CHEAP) is False
    assert is_short_vol_label("neutral") is False
    assert is_short_vol_label("cold (insufficient history)") is False


def test_gated_label_only_tightens_short_vol_in_stress():
    stress = build_regime_gate(40.0)
    calm = build_regime_gate(15.0)
    # rich short-vol in stress → gated off
    assert "GATED OFF" in gated_label(_RICH, stress)
    # rich short-vol in calm → unchanged
    assert gated_label(_RICH, calm) == _RICH
    # cheap (long-vol) in stress → untouched (overlay never loosens/blocks longs)
    assert gated_label(_CHEAP, stress) == _CHEAP


def test_regime_gate_is_frozen_dataclass():
    g = build_regime_gate(20.0)
    assert isinstance(g, RegimeGate)
    with pytest.raises(FrozenInstanceError):
        g.short_vol_allowed = True  # type: ignore[misc]
