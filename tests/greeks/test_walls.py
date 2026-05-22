"""Tests for call/put wall detection — pure, no DB."""

from __future__ import annotations

import pandas as pd
import pytest

from trading_intel.errors import ComputationError
from trading_intel.greeks.walls import compute_walls


def _chain() -> pd.DataFrame:
    # call gxoi peaks at 7500; put gxoi peaks at 7000
    return pd.DataFrame(
        [
            {"strike": 7400, "opt_kind": "call", "gxoi": 10.0},
            {"strike": 7500, "opt_kind": "call", "gxoi": 30.0},
            {"strike": 7600, "opt_kind": "call", "gxoi": 5.0},
            {"strike": 7000, "opt_kind": "put", "gxoi": 25.0},
            {"strike": 7100, "opt_kind": "put", "gxoi": 12.0},
        ]
    )


def test_compute_walls_picks_max_gxoi_per_side():
    w = compute_walls(_chain())
    assert w["call_wall"] == 7500.0
    assert w["call_wall_gxoi"] == 30.0
    assert w["put_wall"] == 7000.0
    assert w["put_wall_gxoi"] == 25.0


def test_compute_walls_sums_duplicate_strikes():
    # two call rows at 7400 (e.g. multiple expiries) sum to beat 7500
    chain = pd.DataFrame(
        [
            {"strike": 7400, "opt_kind": "call", "gxoi": 20.0},
            {"strike": 7400, "opt_kind": "call", "gxoi": 20.0},
            {"strike": 7500, "opt_kind": "call", "gxoi": 30.0},
        ]
    )
    w = compute_walls(chain)
    assert w["call_wall"] == 7400.0  # 40 summed > 30
    assert w["put_wall"] is None  # no puts


def test_compute_walls_errors():
    with pytest.raises(ComputationError):
        compute_walls(pd.DataFrame())
    with pytest.raises(ComputationError):
        compute_walls(pd.DataFrame([{"strike": 1}]))  # missing opt_kind/gxoi
