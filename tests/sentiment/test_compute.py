"""Tests for sentiment derivations — pure, no vendor."""

from __future__ import annotations

from trading_intel.sentiment.compute import SentimentInputs, derived_fields


def test_upside_and_buy_share():
    inp = SentimentInputs(
        symbol="ORCL", pt_avg=252.0, price=133.6, rating_buy=37, rating_hold=5, rating_sell=1
    )
    d = derived_fields(inp)
    assert abs(d["pt_upside_pct"] - (252.0 / 133.6 - 1.0)) < 1e-9
    assert abs(d["buy_share"] - 37 / 43) < 1e-9


def test_missing_inputs_yield_none():
    d = derived_fields(SentimentInputs(symbol="X"))
    assert d["pt_upside_pct"] is None
    assert d["buy_share"] is None


def test_zero_price_is_guarded():
    d = derived_fields(SentimentInputs(symbol="X", pt_avg=100.0, price=0.0))
    assert d["pt_upside_pct"] is None
