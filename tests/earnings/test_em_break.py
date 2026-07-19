"""Tests for the expected-move-break detector — pure, no I/O."""

from __future__ import annotations

import pytest

from trading_intel.earnings.em_break import (
    em_break,
    expected_move_pct,
    over_realization,
    realized_gap_pct,
)


def test_expected_move_and_gap_pct():
    assert expected_move_pct(8.0, 100.0) == pytest.approx(0.08)
    assert realized_gap_pct(100.0, 88.0) == pytest.approx(-0.12)
    assert realized_gap_pct(100.0, 112.0) == pytest.approx(0.12)


def test_em_break_clean_downside_break():
    r = em_break(0.08, -0.12)
    assert r["break_ratio"] == pytest.approx(1.5)
    assert r["sigma"] == pytest.approx(1.2)  # 1.5 * 0.8
    assert r["direction"] == "down"
    assert r["broke"] is True
    assert r["label"] == "break"


def test_em_break_contained_and_violent():
    contained = em_break(0.08, 0.05)
    assert contained["broke"] is False
    assert contained["label"] == "contained"

    violent = em_break(0.08, -0.20)
    assert violent["break_ratio"] == pytest.approx(2.5)
    assert violent["label"] == "violent"
    assert violent["direction"] == "down"


def test_em_break_rejects_bad_em():
    with pytest.raises(ValueError):
        em_break(0.0, -0.10)
    with pytest.raises(ValueError):
        expected_move_pct(8.0, 0.0)


def test_over_realization_persisting_beyond_gap():
    # Down gap that keeps extending: -12% -> -15% -> -18%.
    r = over_realization([-0.12, -0.15, -0.18], em_pct=0.08, gap_pct=-0.12)
    assert r["peak_extension"] == pytest.approx(0.18 / 0.08)
    assert r["gap_extension"] == pytest.approx(0.12 / 0.08)
    assert r["persisting"] is True
    assert r["extended_beyond_gap"] is True
    assert r["retraced_frac"] == pytest.approx(0.0)


def test_over_realization_retracing_stalls():
    # -12% -> -15% -> -5%: retraced most of the move.
    r = over_realization([-0.12, -0.15, -0.05], em_pct=0.08, gap_pct=-0.12)
    assert r["persisting"] is False
    assert r["retraced_frac"] == pytest.approx((0.15 - 0.05) / 0.15)


def test_over_realization_empty_path():
    r = over_realization([], em_pct=0.08, gap_pct=-0.12)
    assert r["persisting"] is False
    assert r["peak_extension"] == pytest.approx(0.0)
