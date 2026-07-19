"""Tests for the historical-reconstruction math (path b, pure)."""

from __future__ import annotations

from datetime import date

import pytest

from trading_intel.backtest.em_break import bars_from_rows
from trading_intel.backtest.reconstruct import (
    em_broke,
    em_pct,
    gap_pct,
    reconstruct_outcome,
    straddle_from_legs,
)


def test_straddle_and_em_pct():
    s = straddle_from_legs(5.0, 4.0)
    assert s == pytest.approx(9.0)
    assert em_pct(s, 100.0) == pytest.approx(0.09)


def test_gap_and_break_detection():
    g = gap_pct(100.0, 112.0)
    assert g == pytest.approx(0.12)
    assert em_broke(g, 0.09) is True
    assert em_broke(gap_pct(100.0, 105.0), 0.09) is False


def test_em_pct_rejects_bad_spot():
    with pytest.raises(ValueError):
        em_pct(9.0, 0.0)


def test_reconstruct_outcome_builds_straddle_structure():
    # entry 100, straddle 9 -> target 109, stop 91
    fwd = bars_from_rows([(date(2026, 7, 2), 110, 104, 109)])
    oc = reconstruct_outcome(100.0, 9.0, fwd, max_days=20)
    assert oc is not None
    assert oc.target == pytest.approx(109.0)
    assert oc.stop == pytest.approx(91.0)
    assert oc.result == "win"
