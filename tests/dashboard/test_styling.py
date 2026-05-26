"""Tests for the dashboard colour-coding helpers."""

from __future__ import annotations

from trading_intel.dashboard.styling import (
    AMBER,
    BLUE,
    GREEN,
    NEUTRAL,
    RED,
    flip_distance_pct,
    flip_proximity_color,
    flip_state,
    gamma_regime_color,
    gex_dir_color,
    richness_color,
    staleness_color,
    zone_color,
)


def test_gex_dir_color():
    assert gex_dir_color("up") == GREEN
    assert gex_dir_color("down") == RED
    assert gex_dir_color(None) == NEUTRAL


def test_gamma_regime_color():
    assert gamma_regime_color("long gamma (above flip)") == GREEN
    assert gamma_regime_color("short gamma") == RED
    assert gamma_regime_color("n/a") == NEUTRAL


def test_zone_color():
    assert (zone_color("low"), zone_color("mid"), zone_color("high")) == (GREEN, AMBER, RED)
    assert zone_color(None) == NEUTRAL


def test_richness_color_bands():
    assert richness_color(90) == AMBER   # rich (sell-vol candidate)
    assert richness_color(10) == BLUE    # cheap (long-vol)
    assert richness_color(50) == NEUTRAL
    assert richness_color(None) == NEUTRAL


def test_staleness_color():
    assert staleness_color("fresh") == GREEN
    assert staleness_color("stale") == RED
    assert staleness_color("unknown") == NEUTRAL


def test_flip_distance_pct():
    # signed distance as a fraction of spot: (spot - flip) / spot.
    assert flip_distance_pct(105.0, 100.0) == (105.0 - 100.0) / 105.0
    assert flip_distance_pct(95.0, 100.0) < 0
    assert flip_distance_pct(None, 100.0) is None
    assert flip_distance_pct(0.0, 100.0) is None


def test_flip_state_and_proximity():
    assert flip_state(110.0, 100.0) == "above flip"
    assert flip_state(90.0, 100.0) == "below flip"
    assert "near flip" in flip_state(100.2, 100.0)  # within 0.5%
    assert flip_state(None, 100.0) == "n/a"
    assert flip_proximity_color(100.2, 100.0) == AMBER
    assert flip_proximity_color(120.0, 100.0) == NEUTRAL
