"""Defined-risk structure economics — pure arithmetic, no market data."""

from __future__ import annotations

from trading_intel.jaguar.structure import call_spread, short_strike_for_move


def test_call_spread_economics():
    s = call_spread("BSX", "Dec", 50, 60, long_price=4.50, short_price=1.80)
    assert s.width == 10
    assert s.debit == 2.70  # net debit = max risk basis
    assert s.max_risk == 270.0
    assert s.max_gain == 730.0
    assert round(s.target_pct, 2) == 2.70  # 730 / 270 ≈ 2.70×
    assert s.breakeven == 52.70
    assert s.label == "BSX Dec 50/60 call spread"


def test_degrades_without_chain_marks():
    s = call_spread("TRMB", "Sep", 55, 65)
    assert s.width == 10
    assert s.debit is None and s.max_risk is None and s.target_pct is None
    assert s.breakeven is None
    assert "55/65 call spread" in s.label


def test_invalid_debit_is_not_priced():
    # debit >= width can't be a real defined-risk spread → economics None
    s = call_spread("X", "Sep", 55, 65, long_price=12.0, short_price=1.0)
    assert s.max_risk is None and s.target_pct is None


def test_short_strike_for_move():
    assert short_strike_for_move(50, target_move_pct=0.20, step=5) == 60
    assert short_strike_for_move(52.91, target_move_pct=0.20, step=5) == 65
