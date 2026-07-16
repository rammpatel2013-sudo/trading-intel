"""Tests for the FMP -> FactorInputs mapping — pure."""

from __future__ import annotations

import numpy as np
import pytest

from trading_intel.factors.fmp_map import (
    _first_num,
    _rec,
    extract_inputs,
    momentum_returns,
)


def test_first_num_parses_and_skips():
    d = {"a": 3, "b": "4.5", "c": True, "d": "x", "e": None}
    assert _first_num(d, ("z", "a")) == 3.0
    assert _first_num(d, ("b",)) == 4.5
    assert _first_num(d, ("c",)) is None  # bool is not a number here
    assert _first_num(d, ("d", "e")) is None


def test_rec_normalizes_list_dict_none():
    assert _rec([{"x": 1}]) == {"x": 1}
    assert _rec({"x": 1}) == {"x": 1}
    assert _rec(None) == {}
    assert _rec([]) == {}


def test_momentum_returns_from_closes():
    closes = np.arange(1, 301, dtype=float)  # 300 sessions
    r3, r12 = momentum_returns(closes)
    assert r3 == pytest.approx(300 / 237 - 1)  # arr[-64] == 237
    assert r12 == pytest.approx(300 / 48 - 1)  # arr[-253] == 48


def test_momentum_returns_insufficient_history():
    r3, r12 = momentum_returns(np.arange(1, 51, dtype=float))  # only 50 sessions
    assert r3 is None and r12 is None
    assert momentum_returns(None) == (None, None)


def test_extract_inputs_merges_payloads():
    inp = extract_inputs(
        "AAPL",
        profile=[{"beta": 1.3}],
        ratios=[
            {
                "peRatioTTM": 15.0,
                "priceToBookRatioTTM": 3.0,
                "returnOnEquityTTM": 0.25,
                "debtEquityRatioTTM": "0.5",  # string -> parsed
            }
        ],
        key_metrics=[{"returnOnInvestedCapitalTTM": 0.18, "peRatioTTM": 99.0}],  # ratios win
        growth=[{"revenueGrowth": 0.12, "epsgrowth": 0.20}],
        closes=np.arange(1, 301, dtype=float),
    )
    assert inp.symbol == "AAPL"
    assert inp.pe == 15.0  # ratios-ttm overrides key-metrics
    assert inp.pb == 3.0
    assert inp.roe == 0.25
    assert inp.roic == 0.18
    assert inp.debt_to_equity == 0.5
    assert inp.revenue_growth == 0.12
    assert inp.eps_growth == 0.20
    assert inp.beta == 1.3
    assert inp.ret_3m is not None and inp.ret_12m is not None


def test_extract_inputs_all_missing_is_safe():
    inp = extract_inputs("X")
    assert inp.symbol == "X"
    assert inp.pe is None and inp.beta is None and inp.ret_12m is None


def test_extract_inputs_real_fmp_keys():
    """Regression: the actual CVForge/FMP key spellings (probe 2026-07-16)."""
    inp = extract_inputs(
        "AAPL",
        profile=[{"beta": 1.1}],
        ratios=[
            {
                "priceToEarningsRatioTTM": 30.0,
                "enterpriseValueMultipleTTM": 25.0,
                "debtToEquityRatioTTM": 1.5,
                "operatingCashFlowSalesRatioTTM": 0.30,
                "grossProfitMarginTTM": 0.45,
            }
        ],
        key_metrics=[{"returnOnEquityTTM": 1.4, "returnOnInvestedCapitalTTM": 0.5}],
        growth=[{"revenueGrowth": 0.06}],
    )
    assert inp.pe == 30.0
    assert inp.debt_to_equity == 1.5
    assert inp.fcf_margin == 0.30
    assert inp.ev_ebitda == 25.0
    assert inp.roe == 1.4
