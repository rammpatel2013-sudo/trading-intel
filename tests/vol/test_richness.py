"""Tests for the volatility-richness ranking layer."""

from __future__ import annotations

import numpy as np
import pytest

from trading_intel.vol.richness import (
    RichnessInputs,
    atm_iv_at_horizon,
    build_richness_row,
    classify_richness,
    compute_vrp,
    iv_rank,
    percentile_rank,
    rank_richness,
)

# ── ATM-IV term-structure interpolation ────────────────────────────────


def test_atm_iv_interp_flat_term_structure():
    # Flat IV term structure → interpolated IV equals the flat level.
    iv = atm_iv_at_horizon([21, 42, 63], [0.20, 0.20, 0.20], 30)
    assert iv == pytest.approx(0.20)


def test_atm_iv_interp_total_variance_midpoint():
    # Two expiries, equal-time bracket of the horizon: interpolate in iv^2*t.
    dte = [20, 40]
    ivs = [0.20, 0.30]
    h = 30
    w0, w1 = 0.20**2 * 20, 0.30**2 * 40
    expected = np.sqrt((w0 + (w1 - w0) * (h - 20) / (40 - 20)) / h)
    assert atm_iv_at_horizon(dte, ivs, h) == pytest.approx(expected)


def test_atm_iv_interp_clamps_outside_range():
    assert atm_iv_at_horizon([30, 60], [0.18, 0.22], 10) == pytest.approx(0.18)  # below
    assert atm_iv_at_horizon([30, 60], [0.18, 0.22], 200) == pytest.approx(0.22)  # above


def test_atm_iv_interp_none_when_no_usable_expiries():
    assert atm_iv_at_horizon([np.nan, 0], [np.nan, 0.2], 30) is None
    assert atm_iv_at_horizon([], [], 30) is None


# ── VRP + standardization math ─────────────────────────────────────────


def test_compute_vrp_sign():
    assert compute_vrp(0.25, 0.18) == pytest.approx(0.07)  # rich
    assert compute_vrp(0.15, 0.20) == pytest.approx(-0.05)  # cheap


def test_percentile_rank_value_and_cold():
    hist = list(np.linspace(-0.05, 0.05, 50))
    # current at the median → ~0.5 percentile.
    assert percentile_rank(hist, 0.0) == pytest.approx(0.5, abs=0.03)
    # below minimum history → cold (None).
    assert percentile_rank([0.01, 0.02], 0.015) is None


def test_iv_rank_value_range_and_degenerate():
    hist = list(np.linspace(0.10, 0.30, 40))
    assert iv_rank(hist, 0.20) == pytest.approx(0.5, abs=0.02)
    assert iv_rank(hist, 0.10) == pytest.approx(0.0, abs=0.01)
    # degenerate (flat) history → None even with enough points.
    assert iv_rank([0.2] * 40, 0.2) is None


def test_classify_richness_thresholds():
    assert "rich" in classify_richness(0.9)
    assert "cheap" in classify_richness(0.1)
    assert classify_richness(0.5) == "neutral"
    assert "cold" in classify_richness(None)


# ── row builder + ranking frame ────────────────────────────────────────


def _inputs(symbol: str, iv: float, fcst: float, *, n_hist: int = 40) -> RichnessInputs:
    rng = np.random.default_rng(abs(hash(symbol)) % 2**32)
    vrp_hist = list(rng.normal(0.0, 0.02, n_hist))
    iv_hist = list(rng.normal(0.2, 0.03, n_hist))
    return RichnessInputs(
        symbol=symbol, horizon_dte=30, iv_atm=iv, forecast_rv=fcst,
        iv_history=iv_hist, vrp_history=vrp_hist,
    )


def test_build_richness_row_scores_rich_name():
    # iv far above forecast and above its own VRP history → high percentile, rich.
    row = build_richness_row(_inputs("AAA", 0.40, 0.18))
    assert row.vrp_pts == pytest.approx(0.22)
    assert row.vrp_pctile == pytest.approx(1.0)
    assert row.richness_score == row.vrp_pctile
    assert "rich" in row.label


def test_build_richness_row_cold_start():
    inp = RichnessInputs(
        symbol="bbb", horizon_dte=60, iv_atm=0.25, forecast_rv=0.20,
        iv_history=[0.2, 0.21], vrp_history=[0.01, 0.02],
    )
    row = build_richness_row(inp)
    assert row.symbol == "BBB"
    assert row.vrp_pctile is None and row.iv_rank is None
    assert row.richness_score is None
    assert "cold" in row.label


def test_rank_richness_orders_richest_first_cold_last():
    rich = build_richness_row(_inputs("RICH", 0.40, 0.15))  # high pctile
    cheap = build_richness_row(_inputs("CHEP", 0.10, 0.25))  # low pctile
    cold = build_richness_row(
        RichnessInputs("COLD", 30, 0.3, 0.2, iv_history=[0.2], vrp_history=[0.0])
    )
    frame = rank_richness([cheap, cold, rich])
    order = list(frame["symbol"])
    assert order.index("RICH") < order.index("CHEP")  # richer ranks above cheaper
    assert order[-1] == "COLD"  # cold (no score) sinks to the bottom


def test_rank_richness_empty_frame_has_columns():
    frame = rank_richness([])
    assert frame.empty
    assert "richness_score" in frame.columns
