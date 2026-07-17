"""Tests for the vol-surface footprint read — pure, no vendor/DB."""

from __future__ import annotations

from trading_intel.vol.footprint import _drift, analyze_footprint


def test_drift_basis_points_and_direction():
    w = _drift("x", [0.13, 0.12])  # -1.00 vol pt = -100 bp
    assert abs(w.total_bp - (-100.0)) < 1e-6
    assert w.direction == "offered" and w.persistence == 1.0


def test_calls_offered_persistently_reads_long_gamma_and_confirms_gex():
    calls = [0.145, 0.142, 0.139, 0.136, 0.133]  # marked down every day
    puts = [0.170, 0.170, 0.171, 0.170, 0.170]   # flat
    r = analyze_footprint(call_ivs=calls, put_ivs=puts, net_gex=5000.0)
    assert r.call.direction == "offered" and r.call.persistence == 1.0
    assert "long gamma" in r.regime
    assert r.gex_sign == "long" and r.confirms_gex is True
    assert r.headline and r.narrative


def test_calls_offered_but_gex_short_contradicts():
    calls = [0.145, 0.142, 0.139, 0.136, 0.133]
    puts = [0.170, 0.170, 0.170, 0.170, 0.170]
    r = analyze_footprint(call_ivs=calls, put_ivs=puts, net_gex=-3000.0)
    assert "long gamma" in r.regime and r.gex_sign == "short"
    assert r.confirms_gex is False and "CONTRADICTS" in r.headline


def test_put_bid_reads_crash_protection():
    calls = [0.13, 0.13, 0.13, 0.13]
    puts = [0.160, 0.165, 0.172, 0.180]  # bid up every day
    r = analyze_footprint(call_ivs=calls, put_ivs=puts, net_gex=None)
    assert r.put.direction == "bid" and "crash-protection" in r.regime
    assert r.gex_sign is None and r.confirms_gex is None


def test_flat_is_no_clean_footprint():
    r = analyze_footprint(call_ivs=[0.13, 0.1301, 0.1299, 0.13], put_ivs=[0.16, 0.16, 0.16, 0.16])
    assert "no clean footprint" in r.regime
