"""Tests for the swing-setup generator's pure gating (no Postgres)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime

import pytest

from trading_intel.strategies.swing_options import (
    SIGNAL_LONG,
    SIGNAL_SHORT,
    FeatureRow,
    evaluate,
)

_AS_OF = date(2026, 7, 16)


def _bull_cheap(sym: str = "AAPL") -> FeatureRow:
    return FeatureRow(
        symbol=sym,
        ts=_AS_OF,
        px_vs_sma50=0.05,
        rsi14=60,
        dex=1_000_000,
        iv_rv=1.0,
        gex=500_000,
        atm_iv=0.25,
        atm_iv_rank_252d=0.20,
    )


def _bear_rich(sym: str = "TSLA") -> FeatureRow:
    return FeatureRow(
        symbol=sym,
        ts=_AS_OF,
        px_vs_sma50=-0.05,
        rsi14=40,
        dex=-1_000_000,
        iv_rv=1.4,
        gex=500_000,
        atm_iv=0.6,
        atm_iv_rank_252d=0.80,
    )


def test_bullish_cheap_vol_emits_long_experimental():
    (sig,) = evaluate([_bull_cheap()], as_of=_AS_OF)
    assert sig.signal_type == SIGNAL_LONG
    assert sig.symbol == "AAPL"
    assert sig.payload["experimental"] is True
    assert sig.payload["vol_context"] == "cheap"
    assert sig.confidence == pytest.approx(0.9)  # 1.0 * (0.6 + |0.2-0.5|)
    assert sig.ts == datetime(2026, 7, 16, 0, 0)


def test_bearish_rich_vol_emits_short():
    (sig,) = evaluate([_bear_rich()], as_of=_AS_OF)
    assert sig.signal_type == SIGNAL_SHORT
    assert sig.payload["vol_context"] == "rich"
    assert sig.confidence == pytest.approx(0.54)  # 0.6 * (0.6 + |0.8-0.5|)


def test_mid_range_iv_rank_has_no_vol_edge():
    row = replace(_bull_cheap(), atm_iv_rank_252d=0.50)
    assert evaluate([row], as_of=_AS_OF) == []


def test_unmatured_percentile_is_skipped():
    row = replace(_bull_cheap(), atm_iv_rank_252d=None)
    assert evaluate([row], as_of=_AS_OF) == []


def test_low_conviction_skipped():
    weak = FeatureRow(symbol="X", ts=_AS_OF, px_vs_sma50=0.01, atm_iv_rank_252d=0.2)  # only trend
    assert evaluate([weak], as_of=_AS_OF) == []


def test_min_score_override_gates():
    assert evaluate([_bull_cheap()], as_of=_AS_OF, min_score=101) == []


def test_to_row_matches_signal_columns():
    (sig,) = evaluate([_bull_cheap()], as_of=_AS_OF)
    row = sig.to_row()
    assert set(row) == {"ts", "symbol", "signal_type", "payload", "confidence"}
    assert row["signal_type"] == SIGNAL_LONG
