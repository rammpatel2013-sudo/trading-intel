"""Tests for the implied-vol surface grid builder — pure, no network."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from trading_intel.errors import ComputationError
from trading_intel.greeks.surface import build_surface_grid


def _chain(ref: date) -> pd.DataFrame:
    """Synthetic chain: 3 expiries, strikes 80..120 (spot=100), a simple smile."""
    rows = []
    for dte in (30, 60, 90):
        exp = pd.Timestamp(ref) + pd.Timedelta(days=dte)
        for strike in range(80, 121, 5):
            moneyness = strike / 100.0
            iv = 0.18 + 0.10 * (moneyness - 1.0) ** 2  # U-shaped smile, min at ATM
            rows.append({"expiration": exp, "strike": float(strike), "opt_kind": "call", "iv": iv})
            rows.append({"expiration": exp, "strike": float(strike), "opt_kind": "put", "iv": iv})
    return pd.DataFrame(rows)


def test_surface_shape_and_axes():
    ref = date(2026, 5, 21)
    surf = build_surface_grid(_chain(ref), spot=100.0, moneyness_steps=41, ref=ref)
    assert surf.iv.shape == (3, 41)
    assert list(surf.dte) == [30, 60, 90]
    assert surf.n_expiries == 3
    assert surf.spot == 100.0
    assert surf.moneyness[0] == pytest.approx(0.80)
    assert surf.moneyness[-1] == pytest.approx(1.20)


def test_surface_recovers_smile():
    ref = date(2026, 5, 21)
    surf = build_surface_grid(_chain(ref), spot=100.0, moneyness_steps=41, ref=ref)
    atm = surf.iv[0, 20]  # moneyness == 1.00 (middle of 0.80..1.20)
    assert atm == pytest.approx(0.18, abs=1e-6)
    assert atm < surf.iv[0, 0]  # lower than the downside wing
    assert atm < surf.iv[0, -1]  # lower than the upside wing


def test_surface_errors():
    with pytest.raises(ComputationError):
        build_surface_grid(pd.DataFrame(), spot=100.0)
    with pytest.raises(ComputationError):
        build_surface_grid(_chain(date(2026, 5, 21)), spot=0.0)


# ── delta surface + forward vol ─────────────────────────────────────────────

import numpy as np  # noqa: E402

from trading_intel.greeks.surface import build_delta_surface, forward_vol  # noqa: E402


def _delta_chain(ref: date) -> pd.DataFrame:
    """3 expiries; OTM puts (downside skew) + flatter OTM calls, keyed by delta."""
    rows = []
    for dte in (30, 60, 90):
        exp = pd.Timestamp(ref) + pd.Timedelta(days=dte)
        for d in (5, 10, 15, 20, 25, 30, 35, 40, 45, 50):
            rows.append(
                {
                    "expiration": exp,
                    "opt_kind": "put",
                    "delta": -d / 100,
                    "iv": 0.13 + (50 - d) * 0.002,
                }  # 5Δ put richest (skew)
            )
            rows.append(
                {
                    "expiration": exp,
                    "opt_kind": "call",
                    "delta": d / 100,
                    "iv": 0.13 + (50 - d) * 0.001,
                }
            )
    return pd.DataFrame(rows)


def test_delta_surface_shape_and_skew():
    ref = date(2026, 5, 21)
    surf = build_delta_surface(_delta_chain(ref), n_expiries=3, ref=ref)
    assert surf.iv_put.shape == (3, len(surf.deltas))
    assert list(surf.dte) == [30, 60, 90]
    assert surf.expiries[0].isoformat() == "2026-06-20"
    # downside skew: 5Δ put IV > 50Δ put IV
    assert surf.iv_put[0, 0] > surf.iv_put[0, -1]
    # ATM ~ 13% (50Δ both wings)
    assert surf.atm_iv[0] == pytest.approx(0.13, abs=1e-6)


def test_forward_vol_flat_term():
    dte = np.array([30, 60, 90])
    atm = np.array([0.13, 0.13, 0.13])
    fwd = forward_vol(dte, atm)
    assert fwd[0] == pytest.approx(0.13)
    assert fwd[1] == pytest.approx(0.13, abs=1e-6)
    assert fwd[2] == pytest.approx(0.13, abs=1e-6)


def test_forward_vol_upward_term_gives_higher_forward():
    dte = np.array([30, 60])
    atm = np.array([0.15, 0.18])  # rising term structure
    fwd = forward_vol(dte, atm)
    assert fwd[1] > atm[1]  # forward vol exceeds spot vol when term rises
