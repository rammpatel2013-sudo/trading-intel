"""Tests for the Track B credit-income ranking — pure."""

from __future__ import annotations

import pytest

from trading_intel.swing.credit_income import credit_income_score, rank_universe


def test_rich_bullish_scores_put_credit_spread():
    feat = {
        "symbol": "A",
        "px_vs_sma50": 0.05,
        "rsi14": 60,
        "dex": 1_000_000,
        "iv_rv": 1.4,
        "gex": 500_000,
        "skew_25d": 0.05,
        "atm_iv": 0.30,
    }
    idea = credit_income_score(feat)
    assert idea.lean == "bullish"
    assert idea.side == "put"
    assert "Bull put credit spread" in idea.structure
    assert idea.score == pytest.approx(70.0)  # 50 abs + 20 put-skew, no xs rank


def test_rich_bearish_scores_call_credit_spread():
    feat = {
        "symbol": "B",
        "px_vs_sma50": -0.05,
        "rsi14": 40,
        "dex": -1_000_000,
        "iv_rv": 1.35,
        "skew_25d": -0.04,
        "atm_iv": 0.5,
    }
    idea = credit_income_score(feat)
    assert idea.side == "call"
    assert idea.score == pytest.approx(70.0)  # 50 abs + 20 call-skew alignment


def test_cheap_vol_scores_low():
    idea = credit_income_score({"symbol": "C", "iv_rv": 0.8, "px_vs_sma50": 0.1, "rsi14": 60})
    assert idea.score == pytest.approx(5.0)  # cheap vol -> poor to sell


def test_neutral_lean_is_iron_condor():
    idea = credit_income_score({"symbol": "D", "iv_rv": 1.2, "skew_25d": 0.03})
    assert idea.side == "iron_condor"
    assert "Iron condor" in idea.structure


def test_rank_universe_orders_richest_first_with_xs_rank():
    feats = [
        {"symbol": "A", "iv_rv": 1.5, "px_vs_sma50": 0.05, "rsi14": 60, "dex": 1, "skew_25d": 0.05},
        {"symbol": "B", "iv_rv": 1.0, "px_vs_sma50": 0.05, "rsi14": 60, "dex": 1, "skew_25d": 0.05},
        {
            "symbol": "C",
            "iv_rv": 1.2,
            "px_vs_sma50": -0.05,
            "rsi14": 40,
            "dex": -1,
            "skew_25d": -0.03,
        },
    ]
    ranked = rank_universe(feats)
    assert [i.symbol for i in ranked] == ["A", "C", "B"]
    assert ranked[0].iv_rv_rank == pytest.approx(1.0)  # richest of the batch
    assert ranked[0].score == pytest.approx(100.0)  # 50 + 30*1.0 + 20 put-skew


def test_rank_universe_handles_missing_iv_rv():
    ideas = rank_universe(
        [{"symbol": "X"}, {"symbol": "Y", "iv_rv": 1.4, "px_vs_sma50": 0.1, "rsi14": 60}]
    )
    by = {i.symbol: i for i in ideas}
    assert by["X"].iv_rv_rank is None
    assert by["Y"].iv_rv_rank == pytest.approx(1.0)
