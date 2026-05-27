"""Tests for the spot-ladder MM dollar-gamma profile (ADR-002)."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from trading_intel.greeks.gamma_profile import ALL_COL, gamma_profile

_REF = date(2026, 5, 26)


def test_per_expiry_columns_sum_to_all():
    chain = pd.DataFrame(
        {
            "opt_kind": ["call", "call"],
            "strike": [7500, 7500],
            "expiration": [date(2026, 5, 29), date(2026, 7, 17)],
            "iv": [0.18, 0.20],
            "oi": [5000, 2000],
        }
    )
    prof = gamma_profile(chain, 7475.0, ref=_REF, n_points=41, span=0.07)
    expiry_cols = [c for c in prof.columns if c != ALL_COL]
    assert expiry_cols == ["2026-05-29", "2026-07-17"]  # sorted ascending
    assert np.allclose(prof[ALL_COL], prof[expiry_cols].sum(axis=1))


def test_long_call_book_positive_and_peaks_near_strike():
    chain = pd.DataFrame(
        {
            "opt_kind": ["call"],
            "strike": [7500],
            "expiration": [date(2026, 5, 29)],
            "iv": [0.18],
            "oi": [5000],
        }
    )
    prof = gamma_profile(chain, 7475.0, ref=_REF, n_points=81, span=0.07)
    assert (prof[ALL_COL] > 0).all()  # dealers long gamma on long calls (calls +)
    assert 7400 < float(prof[ALL_COL].idxmax()) < 7600  # gamma peaks near the strike


def test_flip_crosses_zero():
    # puts below spot (−), calls above (+) -> net dealer gamma flips sign across spot
    chain = pd.DataFrame(
        {
            "opt_kind": ["put", "call"],
            "strike": [7300, 7700],
            "expiration": [date(2026, 5, 29), date(2026, 5, 29)],
            "iv": [0.22, 0.18],
            "oi": [8000, 8000],
        }
    )
    prof = gamma_profile(chain, 7500.0, ref=_REF, n_points=61, span=0.06)
    assert prof[ALL_COL].iloc[0] * prof[ALL_COL].iloc[-1] < 0


def test_empty_and_invalid():
    assert gamma_profile(pd.DataFrame(), 100.0).empty
    chain = pd.DataFrame(
        {"opt_kind": ["call"], "strike": [100], "expiration": [date(2026, 5, 29)],
         "iv": [0.2], "oi": [10]}
    )
    assert gamma_profile(chain, 0.0).empty  # bad spot
    assert gamma_profile(chain.drop(columns=["iv"]), 100.0).empty  # missing column
