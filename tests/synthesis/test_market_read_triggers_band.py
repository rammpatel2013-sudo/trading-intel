"""Newsletter triggers with a price far from spot are dropped (2026-10-29: Norseman '3450')."""

from __future__ import annotations

from datetime import date

from trading_intel.synthesis import market_read as mr


def _letter(trigger: str) -> dict:
    return {"sources": {"NORSEMAN": {"as_of": "2026-10-22",
                                     "scenarios": [{"trigger": trigger, "consequence": "x"}]}}}


def test_far_price_trigger_is_dropped():
    out = mr._newsletter_triggers(_letter("SPX holds above 3450"), date(2026, 10, 23), spot=7622.86)
    assert out == []


def test_near_price_and_vix_triggers_kept():
    today = date(2026, 10, 23)
    assert mr._newsletter_triggers(_letter("SPX falls below 7,600"), today, spot=7622.86)
    assert mr._newsletter_triggers(_letter("VIX closes above 20"), today, spot=7622.86)
    assert mr._newsletter_triggers(_letter("SPX holds above 3450"), today, spot=None)  # no spot: keep


def test_plausibility_helper():
    assert mr._trigger_plausible("between 7,550 and 7,700", 7622.0)
    assert not mr._trigger_plausible("Huginn target 4,000", 7622.0)
