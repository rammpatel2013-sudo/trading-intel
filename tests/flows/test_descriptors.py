"""Tests for the systematic-flow descriptors — pure, no I/O."""

from __future__ import annotations

import pytest

from trading_intel.flows import (
    CallStrikeChange,
    aggregate_systematic_buying,
    cohort_flow,
    cohort_for,
    exposure_convexity,
    overwriter_call_supply,
    vol_control_exposure,
)


def test_vol_control_exposure_inverse_vol_and_cap():
    assert vol_control_exposure(0.10, target_vol=0.10, w_max=1.5) == pytest.approx(1.0)
    assert vol_control_exposure(0.20, target_vol=0.10, w_max=1.5) == pytest.approx(0.5)
    # target/rv would be 2.0 but the cap binds.
    assert vol_control_exposure(0.05, target_vol=0.10, w_max=1.5) == pytest.approx(1.5)


def test_exposure_convexity_bites_harder_at_low_vol():
    hi = exposure_convexity(0.20, target_vol=0.10)
    lo = exposure_convexity(0.05, target_vol=0.10)
    assert hi < 0 and lo < 0
    assert abs(lo) > abs(hi)  # lower vol -> larger |dw/drv| -> buys harder


def test_cohort_flow_falling_vol_is_buying():
    vc = cohort_for("vol_control")
    est = cohort_flow(vc, 0.16, [0.15, 0.13, 0.11])
    assert est.d_exposure > 0
    assert est.buying_usd > 0
    assert est.rv_terminal == pytest.approx(0.11)


def test_cohort_flow_rising_vol_is_selling():
    vc = cohort_for("vol_control")
    est = cohort_flow(vc, 0.10, [0.15, 0.20, 0.25])
    assert est.d_exposure < 0
    assert est.buying_usd < 0


def test_cta_trend_gate_flips_sign():
    cta = cohort_for("cta")
    up = cohort_flow(cta, 0.16, [0.11], trend_sign=1.0)
    down = cohort_flow(cta, 0.16, [0.11], trend_sign=-1.0)
    assert up.buying_usd > 0
    assert down.buying_usd < 0
    assert up.buying_usd == pytest.approx(-down.buying_usd)


def test_aggregate_systematic_buying():
    vc = cohort_for("vol_control")
    rp = cohort_for("risk_parity")
    ests = [cohort_flow(vc, 0.16, [0.11]), cohort_flow(rp, 0.16, [0.11])]
    agg = aggregate_systematic_buying(ests)
    assert agg["direction"] == "buying"
    assert agg["total_buying_usd"] > 0
    assert agg["n_cohorts"] == 2


def test_overwriter_call_supply_picks_largest_supply_led_strike():
    changes = [
        CallStrikeChange(strike=110, d_oi=5000, d_iv=-0.02, gxoi=1e6),
        CallStrikeChange(strike=120, d_oi=8000, d_iv=-0.01, gxoi=2e6),
        CallStrikeChange(strike=115, d_oi=3000, d_iv=0.01),  # IV up -> not supply-led
    ]
    r = overwriter_call_supply(changes)
    assert r["supply_led"] is True
    assert r["rebuild_strike"] == pytest.approx(120.0)
    assert r["n_supply_strikes"] == 2


def test_overwriter_call_supply_none_when_no_writing():
    changes = [CallStrikeChange(strike=115, d_oi=3000, d_iv=0.01)]
    r = overwriter_call_supply(changes)
    assert r["supply_led"] is False
    assert r["rebuild_strike"] is None
