"""Tests for ``greeks/straddle.py`` + the BS price additions in black_scholes.

Mirrors source: ``trading_intel/greeks/straddle.py`` +
``trading_intel/greeks/black_scholes.py`` (bs_call_price/bs_put_price/norm_cdf).
"""
from __future__ import annotations

import math

import pandas as pd
import pytest

from trading_intel.errors import ComputationError
from trading_intel.greeks.black_scholes import bs_call_price, bs_put_price, norm_cdf
from trading_intel.greeks.straddle import atm_straddle, straddle_decay


def _chain(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["opt_kind", "strike", "iv", "expiration"])


# ── BS price primitives ────────────────────────────────────────────────


def test_norm_cdf_known_values():
    assert float(norm_cdf(0.0)) == pytest.approx(0.5, abs=1e-9)
    assert float(norm_cdf(1.96)) == pytest.approx(0.975, abs=1e-3)


def test_put_call_parity():
    s, k, sig, t, r = 100.0, 105.0, 0.25, 0.5, 0.03
    c = float(bs_call_price(s, k, sig, t, r))
    p = float(bs_put_price(s, k, sig, t, r))
    assert c - p == pytest.approx(s - k * math.exp(-r * t), abs=1e-9)


def test_atm_straddle_known_value_and_symmetry():
    # spot=strike=100, sigma=0.20, t=0.25, r=0 -> straddle ~ 7.98; call==put at r=0.
    c = float(bs_call_price(100.0, 100.0, 0.20, 0.25, 0.0))
    p = float(bs_put_price(100.0, 100.0, 0.20, 0.25, 0.0))
    assert c == pytest.approx(p, abs=1e-9)
    assert c + p == pytest.approx(7.97, abs=0.05)


# ── atm_straddle ────────────────────────────────────────────────────────


def test_atm_straddle_selects_front_expiry_and_atm_strike():
    rows = [
        ("C", 95, 0.30, 7), ("P", 95, 0.30, 7),
        ("C", 100, 0.20, 7), ("P", 100, 0.20, 7),
        ("C", 105, 0.30, 7), ("P", 105, 0.30, 7),
        ("C", 100, 0.25, 30), ("P", 100, 0.25, 30),  # back expiry — ignored
    ]
    out = atm_straddle(_chain(rows), spot=101.0)
    assert out["atm_strike"] == 100.0
    assert out["dte"] == pytest.approx(7.0, abs=0.05)
    assert out["straddle"] > 0
    assert out["atm_iv"] == pytest.approx(0.20, abs=1e-9)
    assert out["upper"] == pytest.approx(out["spot"] + out["straddle"])
    assert out["lower"] == pytest.approx(out["spot"] - out["straddle"])
    assert out["straddle_pct"] == pytest.approx(out["straddle"] / 101.0 * 100.0)


def test_atm_straddle_one_leg_missing_uses_available_iv():
    rows = [("C", 100, 0.22, 5), ("C", 105, 0.30, 5), ("P", 95, 0.30, 5)]
    out = atm_straddle(_chain(rows), spot=100.0)
    assert out["atm_strike"] == 100.0
    assert out["straddle"] > 0
    assert out["atm_iv"] == pytest.approx(0.22, abs=1e-9)


def test_atm_straddle_empty_chain_returns_empty():
    assert atm_straddle(_chain([]), spot=100.0) == {}


def test_atm_straddle_missing_columns_raises():
    df = pd.DataFrame({"opt_kind": ["C"], "strike": [100.0]})  # no iv/expiration
    with pytest.raises(ComputationError):
        atm_straddle(df, spot=100.0)


def test_atm_straddle_bad_spot_raises():
    rows = [("C", 100, 0.2, 7), ("P", 100, 0.2, 7)]
    with pytest.raises(ComputationError):
        atm_straddle(_chain(rows), spot=0.0)


def test_atm_straddle_no_priceable_rows_raises():
    rows = [("C", 100, 0.0, 7), ("P", 100, float("nan"), 7)]  # iv<=0 / nan
    with pytest.raises(ComputationError):
        atm_straddle(_chain(rows), spot=100.0)


# ── straddle_decay ──────────────────────────────────────────────────────


def test_straddle_decay_labels():
    dec = straddle_decay(8.0, 10.0)  # -20%
    assert dec["label"] == "decaying"
    assert dec["charm_supported"] is True
    assert dec["pct_change"] == pytest.approx(-20.0)

    rep = straddle_decay(11.0, 10.0)  # +10%
    assert rep["label"] == "repricing_up"
    assert rep["charm_supported"] is False

    flat = straddle_decay(10.05, 10.0)  # +0.5% inside the 1% band
    assert flat["label"] == "flat"
    assert flat["charm_supported"] is False


def test_straddle_decay_bad_reference_raises():
    with pytest.raises(ComputationError):
        straddle_decay(10.0, 0.0)
    with pytest.raises(ComputationError):
        straddle_decay(float("nan"), 10.0)
