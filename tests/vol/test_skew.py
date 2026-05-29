"""Tests for the per-name skew descriptors layer (``vol.skew``)."""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from trading_intel.greeks.surface import DeltaSurface
from trading_intel.vol.skew import (
    EXTREME_HI,
    EXTREME_LO,
    butterfly,
    classify_rr,
    compose_label,
    extreme_label,
    front_back_slope,
    risk_reversal,
    shift_vs_slide,
    skew_percentile,
    skew_term_curve,
)

# ── Synthetic-surface helpers ──────────────────────────────────────────


def _make_surface(
    *,
    deltas: tuple[float, ...] = (10.0, 25.0, 50.0),
    dtes: tuple[int, ...] = (30, 60, 90, 180),
    iv_put: list[list[float]] | None = None,
    iv_call: list[list[float]] | None = None,
) -> DeltaSurface:
    """Build a hand-crafted DeltaSurface for predictable golden-value tests.

    Shape is (T, D): one row per expiry in ``dtes``, one column per delta in
    ``deltas`` (50Δ = ATM). Defaults model an equity put-skew: put wing > ATM,
    call wing < ATM, with the smirk flattening across tenor.
    """
    if iv_put is None:
        # rows: (30d, 60d, 90d, 180d); cols: (10Δ, 25Δ, 50Δ)
        iv_put = [
            [0.30, 0.25, 0.20],
            [0.28, 0.24, 0.20],
            [0.27, 0.23, 0.20],
            [0.25, 0.22, 0.20],
        ]
    if iv_call is None:
        iv_call = [
            [0.22, 0.21, 0.20],
            [0.22, 0.21, 0.20],
            [0.22, 0.21, 0.20],
            [0.21, 0.205, 0.20],
        ]
    return DeltaSurface(
        deltas=np.array(deltas, dtype=float),
        dte=np.array(dtes, dtype=int),
        expiries=[date(2026, 6, 26 + i) for i in range(len(dtes))][: len(dtes)],
        iv_put=np.array(iv_put, dtype=float),
        iv_call=np.array(iv_call, dtype=float),
        spot=500.0,
        ref=date(2026, 5, 28),
    )


# ── Risk reversal ──────────────────────────────────────────────────────


def test_risk_reversal_equity_convention_is_put_minus_call():
    s = _make_surface()
    # 30d, 25Δ: put = 0.25, call = 0.21 → +0.04 (put bid, positive RR)
    assert risk_reversal(s, delta=25, horizon_dte=30) == pytest.approx(0.04)


def test_risk_reversal_picks_nearest_expiry():
    s = _make_surface(dtes=(30, 60, 90, 180))
    # 45 is closer to 60 than to 30 -- np.argmin breaks ties to the left, but
    # |45-60|=15 < |45-30|=15 is False; tie -> earlier index (30d).
    val30 = risk_reversal(s, delta=25, horizon_dte=30)
    val45 = risk_reversal(s, delta=25, horizon_dte=45)
    # equally distant tie resolves to index 0 (30d), per numpy
    assert val45 == pytest.approx(val30)
    # firmly biased toward 60d when horizon is 50
    assert risk_reversal(s, delta=25, horizon_dte=50) == pytest.approx(0.03)


def test_risk_reversal_returns_none_when_wing_is_nan():
    s = _make_surface(
        iv_put=[
            [np.nan, np.nan, np.nan],
            [0.28, 0.24, 0.20],
            [0.27, 0.23, 0.20],
            [0.25, 0.22, 0.20],
        ]
    )
    # 30d row has NaN puts -> None
    assert risk_reversal(s, delta=10, horizon_dte=30) is None
    # 60d row is fine
    assert risk_reversal(s, delta=10, horizon_dte=60) == pytest.approx(0.06)


def test_risk_reversal_inverted_when_calls_are_richer():
    # Flip wings: call > put -> negative RR ("call bias", MU-style)
    s = _make_surface(
        iv_put=[
            [0.18, 0.195, 0.20],
            [0.18, 0.195, 0.20],
            [0.18, 0.195, 0.20],
            [0.18, 0.195, 0.20],
        ],
        iv_call=[
            [0.26, 0.23, 0.20],
            [0.26, 0.23, 0.20],
            [0.26, 0.23, 0.20],
            [0.26, 0.23, 0.20],
        ],
    )
    assert risk_reversal(s, delta=25, horizon_dte=30) == pytest.approx(-0.035)


# ── Butterfly ──────────────────────────────────────────────────────────


def test_butterfly_is_wing_avg_minus_atm():
    s = _make_surface()
    # 30d, 25Δ: (0.25 + 0.21)/2 - 0.20 = 0.03
    assert butterfly(s, delta=25, horizon_dte=30) == pytest.approx(0.03)
    # 30d, 10Δ: (0.30 + 0.22)/2 - 0.20 = 0.06
    assert butterfly(s, delta=10, horizon_dte=30) == pytest.approx(0.06)


def test_butterfly_returns_none_on_nan_atm():
    s = _make_surface(
        iv_put=[
            [0.30, 0.25, np.nan],   # 30d ATM put NaN
            [0.28, 0.24, 0.20],
            [0.27, 0.23, 0.20],
            [0.25, 0.22, 0.20],
        ],
        iv_call=[
            [0.22, 0.21, np.nan],   # 30d ATM call NaN
            [0.22, 0.21, 0.20],
            [0.22, 0.21, 0.20],
            [0.21, 0.205, 0.20],
        ],
    )
    # atm_iv is nanmean of put/call at the 50Δ column -> NaN for row 0
    assert butterfly(s, delta=25, horizon_dte=30) is None


# ── Term curve + front-back slope ──────────────────────────────────────


def test_skew_term_curve_returns_ascending_dte_pairs():
    s = _make_surface()
    curve = skew_term_curve(s, delta=25)
    assert [d for d, _ in curve] == [30, 60, 90, 180]
    # Default surface: skew flattens with tenor.
    rrs = [r for _, r in curve]
    assert rrs == sorted(rrs, reverse=True)  # monotone decrease


def test_skew_term_curve_skips_nan_rows():
    s = _make_surface(
        iv_put=[
            [np.nan, np.nan, np.nan],   # 30d -> nan, skipped
            [0.28, 0.24, 0.20],
            [0.27, 0.23, 0.20],
            [0.25, 0.22, 0.20],
        ]
    )
    curve = skew_term_curve(s, delta=10)
    assert [d for d, _ in curve] == [60, 90, 180]


def test_front_back_slope_positive_for_flattening_smirk():
    s = _make_surface()
    # 30d RR(25Δ) = 0.04, 180d RR(25Δ) = 0.015 → +0.025 (steeper at front).
    assert front_back_slope(s, delta=25, near_dte=30, far_dte=180) == pytest.approx(0.025)


def test_front_back_slope_none_when_a_leg_missing():
    s = _make_surface(
        iv_put=[
            [np.nan, np.nan, np.nan],
            [0.28, 0.24, 0.20],
            [0.27, 0.23, 0.20],
            [0.25, 0.22, 0.20],
        ]
    )
    assert front_back_slope(s, delta=10, near_dte=30, far_dte=180) is None


# ── Percentile + cold start ────────────────────────────────────────────


def test_skew_percentile_median():
    hist = list(np.linspace(-0.05, 0.05, 50))
    assert skew_percentile(hist, 0.0) == pytest.approx(0.5, abs=0.03)


def test_skew_percentile_cold_under_min_history():
    assert skew_percentile([0.01, 0.02], 0.015) is None


# ── Classify RR ────────────────────────────────────────────────────────


def test_classify_rr_states_and_tiers():
    assert "steep" in classify_rr(3.5)
    assert classify_rr(1.5) == "moderate put bid"
    assert classify_rr(0.0) == "flat"
    assert "inverted" in classify_rr(-1.5)
    assert "extreme call bias" in classify_rr(-3.0)
    assert classify_rr(None) is None
    assert classify_rr(float("nan")) is None


# ── Extreme tail labels ────────────────────────────────────────────────


def test_extreme_label_tail_boundaries():
    assert extreme_label(0.0161) == "tail call bias"      # MU's percentile
    assert extreme_label(EXTREME_LO) == "tail call bias"  # inclusive at lo
    assert extreme_label(EXTREME_HI) == "tail put bid"    # inclusive at hi
    assert extreme_label(0.5) is None
    assert extreme_label(None) is None


# ── Shift vs slide labels ──────────────────────────────────────────────


def test_shift_vs_slide_decomposition():
    # ATM moved 1.2 vol pts, RR unchanged -> shift.
    assert shift_vs_slide(d_atm_iv_pts=1.2, d_rr_pts=0.1) == "shift"
    # ATM unchanged, RR moved -> slide.
    assert shift_vs_slide(d_atm_iv_pts=0.1, d_rr_pts=1.0) == "slide"
    # Both broke -> mixed.
    assert shift_vs_slide(d_atm_iv_pts=1.0, d_rr_pts=1.0) == "mixed"
    # Both quiet -> flat.
    assert shift_vs_slide(d_atm_iv_pts=0.1, d_rr_pts=0.1) == "flat"


def test_shift_vs_slide_none_on_missing_or_nan_inputs():
    assert shift_vs_slide(d_atm_iv_pts=None, d_rr_pts=0.5) is None
    assert shift_vs_slide(d_atm_iv_pts=0.5, d_rr_pts=None) is None
    assert shift_vs_slide(d_atm_iv_pts=float("nan"), d_rr_pts=0.5) is None


# ── Compose label ──────────────────────────────────────────────────────


def test_compose_label_appends_tail_tag():
    label = compose_label(rr_pts=-3.0, pctile_long=0.02)
    assert "extreme call bias" in label
    assert "tail call bias" in label


def test_compose_label_no_tail_when_pctile_mid():
    label = compose_label(rr_pts=1.5, pctile_long=0.45)
    assert label == "moderate put bid"


def test_compose_label_unknown_when_rr_missing():
    label = compose_label(rr_pts=None, pctile_long=None)
    assert label == "unknown"
