"""Tests for the VIX decomposition - validated vs CBOE's published worked example.

The numbers come from CBOE's "VIX Index Decomposition" whitepaper (Aug 2025),
the Aug 2 -> Aug 5 2024 Yen-Carry Unwind example:
  sticky strike  ~ 2.57  (ATM 18.99 -> 21.55 riding the Aug 2 skew)
  parallel shift ~ 7.29  (5185 strike: 28.85 - 21.55)
  30d put excess ~ 1.66  (4960 put: (36.22 - 27.27) - parallel)
  10d put excess ~ 3.43  (4365 put: (50.43 - 39.71) - parallel)
Small rounding differences vs the whitepaper text are within tolerance.
"""

from __future__ import annotations

import pandas as pd
import pytest

from trading_intel.errors import ComputationError
from trading_intel.greeks.vix_decomposition import (
    decompose,
    interpolate_to_30d,
    skew_from_chain,
)


def _chain(rows: list[tuple[float, str, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["strike", "cp", "iv", "delta"])


# CBOE Yen-Carry worked example, expressed as two 30-day fixed-strike skews.
_PREV = _chain(
    [
        (4365, "P", 39.71, -0.10),
        (4960, "P", 27.27, -0.30),
        (5186, "P", 21.55, -0.50),
        (5346, "C", 18.99, 0.50),
        (5412, "C", 18.50, 0.30),
        (6000, "C", 17.00, 0.10),
    ]
)
_NOW = _chain(
    [
        (4365, "P", 50.43, -0.10),
        (4960, "P", 36.22, -0.30),
        (5186, "P", 28.90, -0.50),
        (5186, "C", 28.85, 0.50),
        (5412, "C", 24.00, 0.30),
        (6000, "C", 21.00, 0.10),
    ]
)


@pytest.fixture
def decomp():
    prev = skew_from_chain(_PREV, spot=5346.0)
    now = skew_from_chain(_NOW, spot=5186.0)
    return decompose(prev, now)


def test_matches_cboe_yen_carry_belly(decomp):
    assert decomp.sticky_strike == pytest.approx(2.57, abs=0.05)
    assert decomp.parallel_shift == pytest.approx(7.29, abs=0.05)


def test_matches_cboe_yen_carry_put_shoulder_and_wing(decomp):
    assert decomp.put_gradient == pytest.approx(1.66, abs=0.05)
    assert decomp.down_convexity == pytest.approx(3.43, abs=0.05)


def test_call_side_was_sold(decomp):
    # Whitepaper: calls contributed negatively (sold to fund downside).
    assert decomp.call_gradient < 0
    assert decomp.up_convexity < 0


def test_dominant_and_regime_read(decomp):
    assert decomp.dominant == "parallel_shift"
    read = decomp.regime_read()
    assert "parallel-shift dominated" in read
    assert "risk-off" in read


def test_interpolate_flat_term_structure_preserves_iv():
    near = pd.DataFrame({"strike": [4000, 5000, 6000], "iv": [20.0, 20.0, 20.0]})
    far = pd.DataFrame({"strike": [4000, 5000, 6000], "iv": [20.0, 20.0, 20.0]})
    out = interpolate_to_30d(near, far, t1=23, t2=37)
    assert out["iv"].tolist() == pytest.approx([20.0, 20.0, 20.0])


def test_interpolate_only_common_strikes():
    near = pd.DataFrame({"strike": [4000, 5000], "iv": [22.0, 18.0]})
    far = pd.DataFrame({"strike": [5000, 6000], "iv": [19.0, 17.0]})
    out = interpolate_to_30d(near, far, t1=23, t2=37)
    assert out["strike"].tolist() == [5000]


def test_skew_from_chain_rejects_bad_input():
    with pytest.raises(ComputationError):
        skew_from_chain(pd.DataFrame(columns=["strike", "cp", "iv"]), spot=100.0)
    with pytest.raises(ComputationError):
        skew_from_chain(pd.DataFrame({"foo": [1]}), spot=100.0)
