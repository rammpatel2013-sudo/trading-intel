"""Tests for the multi-factor cross-sectional compute — pure, deterministic."""

from __future__ import annotations

import pytest

from trading_intel.factors.compute import (
    FactorInputs,
    _zscores,  # helper under test
    compute_factor_scores,
    inputs_from_mapping,
)


def test_zscores_basics():
    assert _zscores([1.0, 1.0, 1.0]) == [0.0, 0.0, 0.0]  # zero spread
    assert _zscores([None, 5.0]) == [None, 0.0]  # <2 present
    z = _zscores([None, 1.0, 3.0])
    assert z[0] is None
    assert z[1] == pytest.approx(-1.0)
    assert z[2] == pytest.approx(1.0)


def test_value_factor_rewards_cheapness():
    # only P/E populated -> value factor = sign-adjusted z of pe (lower is better)
    inp = [FactorInputs("A", pe=10), FactorInputs("B", pe=20), FactorInputs("C", pe=30)]
    scores = {s.symbol: s for s in compute_factor_scores(inp)}
    assert scores["A"].value == pytest.approx(1.2247448, rel=1e-5)  # cheapest -> highest
    assert scores["B"].value == pytest.approx(0.0, abs=1e-9)
    assert scores["C"].value == pytest.approx(-1.2247448, rel=1e-5)
    # unpopulated factors are None
    assert scores["A"].growth is None and scores["A"].risk is None


def test_quality_leverage_is_penalized():
    # higher debt_to_equity should LOWER quality (orientation -1)
    inp = [
        FactorInputs("A", roe=0.30, debt_to_equity=0.1),
        FactorInputs("B", roe=0.10, debt_to_equity=2.0),
    ]
    s = {x.symbol: x for x in compute_factor_scores(inp)}
    assert s["A"].quality > s["B"].quality


def test_composite_is_mean_of_available_factors_equal_weights():
    # A dominates B on value + momentum; composite should rank A above B
    inp = [
        FactorInputs("A", pe=10, ret_12m=0.5),
        FactorInputs("B", pe=30, ret_12m=-0.2),
    ]
    s = {x.symbol: x for x in compute_factor_scores(inp)}
    # value & momentum present (2 factors), each +/-1 z; composite = mean of the two
    assert s["A"].composite == pytest.approx(1.0)
    assert s["B"].composite == pytest.approx(-1.0)


def test_empty_universe():
    assert compute_factor_scores([]) == []


def test_custom_weights_shift_composite():
    inp = [FactorInputs("A", pe=10, ret_12m=-1.0), FactorInputs("B", pe=30, ret_12m=1.0)]
    # weight value only -> A (cheap) wins; weight momentum only -> B wins
    val = {x.symbol: x for x in compute_factor_scores(inp, weights={"value": 1.0})}
    mom = {x.symbol: x for x in compute_factor_scores(inp, weights={"momentum": 1.0})}
    assert val["A"].composite > val["B"].composite
    assert mom["B"].composite > mom["A"].composite


def test_inputs_from_mapping_filters_unknown_and_nonnumeric():
    inp = inputs_from_mapping("A", {"pe": 12.0, "junk": "x", "roe": None, "beta": 1.1})
    assert inp.symbol == "A"
    assert inp.pe == 12.0
    assert inp.beta == 1.1
    assert inp.roe is None
