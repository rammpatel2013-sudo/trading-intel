"""Tests for the Stage-1 swing scorer — pure, deterministic."""

from __future__ import annotations

from trading_intel.swing.scoring import score_setup


def test_full_bullish_setup_scores_100_long_premium():
    feat = {"px_vs_sma50": 0.05, "rsi14": 60, "dex": 1_000_000, "iv_rv": 1.0, "gex": 500_000}
    s = score_setup(feat)
    assert s.score == 100.0
    assert s.lean == "bullish"
    assert "Call debit spread" in s.structure
    assert "long premium favored" in s.structure


def test_rich_vol_bullish_prefers_credit_structure():
    feat = {"px_vs_sma50": 0.05, "rsi14": 60, "dex": 1_000_000, "iv_rv": 1.5, "gex": 500_000}
    s = score_setup(feat)
    assert s.lean == "bullish"
    assert s.structure == "Bull put credit spread (harvest put skew)"  # rich -> no long-prem tag


def test_bearish_setup_leans_and_scores():
    feat = {"px_vs_sma50": -0.05, "rsi14": 40, "dex": -1_000_000, "iv_rv": 1.4, "gex": 500_000}
    s = score_setup(feat)
    assert s.lean == "bearish"
    assert s.score == 60.0  # 0(trend) + 20(rsi) + 15(dex) + 5(iv_rv) + 20(gex)
    assert s.structure == "Bear call credit spread"


def test_conflicting_votes_net_neutral():
    # trend up (+1) vs RSI oversold (-1), no DEX -> direction 0 -> neutral
    s = score_setup({"px_vs_sma50": 0.02, "rsi14": 40})
    assert s.lean == "neutral"


def test_empty_features_no_edge():
    s = score_setup({})
    assert s.score == 0.0
    assert s.lean == "neutral"
    assert s.structure == "No edge — wait"


def test_legacy_report_keys_are_tolerated():
    # report uses spot/sma50/rsi rather than px_vs_sma50/rsi14
    s = score_setup(
        {"spot": 105.0, "sma50": 100.0, "rsi": 62, "dex": 1.0, "iv_rv": 1.0, "gex": 1.0}
    )
    assert s.lean == "bullish"
    assert s.score == 98.8  # RSI kernel peaks at 60; 62 -> 18.8 of 20


def test_score_capped_at_100():
    feat = {"px_vs_sma50": 1.0, "rsi14": 60, "dex": 1.0, "iv_rv": 0.5, "gex": 1.0}
    assert score_setup(feat).score <= 100.0
