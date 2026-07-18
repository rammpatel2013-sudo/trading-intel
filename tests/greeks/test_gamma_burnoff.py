"""Tests for the front-expiry gamma burn-off tracker — pure, no I/O."""

from __future__ import annotations

import pytest

from trading_intel.greeks.gamma_burnoff import burnoff_state, front_dte_share, phase


def test_front_dte_share_basic():
    r = front_dte_share([(2, 100.0), (9, 50.0), (30, 50.0)])
    assert r["front_dte"] == pytest.approx(2.0)
    assert r["total_abs"] == pytest.approx(200.0)
    assert r["front_share"] == pytest.approx(0.5)
    assert r["near_share"] == pytest.approx(0.5)  # only dte<=7 is the front bucket
    assert r["n_expirations"] == 3


def test_front_dte_share_uses_absolute_gex():
    r = front_dte_share([(2, -100.0), (30, 100.0)])
    assert r["front_share"] == pytest.approx(0.5)


def test_front_dte_share_empty():
    r = front_dte_share([])
    assert r["front_share"] == 0.0
    assert r["front_dte"] is None


def test_phase_from_spot_ladder():
    assert phase(90.0, 100.0) == "mechanical"
    assert phase(50.0, 100.0) == "transition"
    assert phase(10.0, 100.0) == "linear"


def test_phase_proxy_and_unknown():
    assert phase(None, None, front_share=0.6) == "mechanical"
    assert phase(None, None, front_share=0.3) == "transition"
    assert phase(None, None, front_share=0.1) == "linear"
    assert phase(None, None) == "unknown"


def test_burnoff_state_expired_front():
    r = burnoff_state([(0, 10.0), (30, 90.0)], dte_to_front_opex=0)
    assert r["burned_off"] is True
    assert r["dte_to_front_opex"] == pytest.approx(0.0)


def test_burnoff_state_share_decay_and_tiny_front():
    # Front book already tiny (0.1%) -> effectively burned off even before OPEX.
    r = burnoff_state(
        [(2, 1.0), (30, 999.0)],
        dte_to_front_opex=5,
        prev_front_share=0.6,
    )
    assert r["burned_off"] is True
    assert r["front_share"] < 0.05
    assert r["share_decay"] == pytest.approx(r["front_share"] - 0.6)
