"""Tests for the seasonality overlay (pure)."""

from __future__ import annotations

from datetime import date

from trading_intel.dashboard.seasonality import seasonal_context


def test_sell_in_may_window():
    ctx = seasonal_context(date(2026, 5, 24))
    assert ctx.in_sell_in_may is True
    assert ctx.half == "weak"
    assert ctx.weekday == "Sunday"
    assert "Sell in May" in ctx.note


def test_strong_half():
    ctx = seasonal_context(date(2026, 1, 15))
    assert ctx.in_sell_in_may is False
    assert ctx.half == "strong"
    assert "Nov-Apr" in ctx.half_label


def test_boundaries():
    assert seasonal_context(date(2026, 4, 30)).half == "strong"  # Apr = strong
    assert seasonal_context(date(2026, 5, 1)).half == "weak"  # May = weak
    assert seasonal_context(date(2026, 10, 31)).half == "weak"  # Oct = weak
    assert seasonal_context(date(2026, 11, 1)).half == "strong"  # Nov = strong
