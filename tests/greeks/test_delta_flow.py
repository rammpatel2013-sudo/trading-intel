"""Tests for the traded delta-notional flow split."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from trading_intel.greeks.delta_flow import MULTIPLIER, delta_notional_split


def _chain() -> pd.DataFrame:
    # Two expiries; calls (delta +), puts (delta -); volume in contracts.
    return pd.DataFrame(
        {
            "opt_kind": ["call", "put", "call", "put"],
            "expiration": ["2026-05-27", "2026-05-27", "2026-06-20", "2026-06-20"],
            "delta": [0.50, -0.40, 0.30, -0.20],
            "volume": [1000, 800, 500, 400],
        }
    )


def test_delta_notional_split_all_and_next():
    spot = 100.0
    out = delta_notional_split(_chain(), spot)
    assert out is not None
    m = spot * MULTIPLIER
    # all expiries
    assert out.call_notional_all == pytest.approx((0.50 * 1000 + 0.30 * 500) * m)
    assert out.put_notional_all == pytest.approx((-0.40 * 800 + -0.20 * 400) * m)
    # next expiry only (2026-05-27)
    assert out.call_notional_next == pytest.approx(0.50 * 1000 * m)
    assert out.put_notional_next == pytest.approx(-0.40 * 800 * m)
    assert out.next_expiry == date(2026, 5, 27)
    # calls positive, puts negative (delta sign carried through)
    assert out.call_notional_all > 0 and out.put_notional_all < 0


def test_delta_notional_split_guards():
    assert delta_notional_split(_chain(), None) is None
    assert delta_notional_split(_chain(), 0.0) is None
    assert delta_notional_split(pd.DataFrame(), 100.0) is None
    # missing required columns -> None
    assert delta_notional_split(pd.DataFrame({"opt_kind": ["call"]}), 100.0) is None


def test_delta_notional_split_single_expiry_all_equals_next():
    chain = pd.DataFrame(
        {
            "cp": ["C", "P"],
            "expiry": ["2026-05-27", "2026-05-27"],
            "delta": [0.6, -0.5],
            "volume": [100, 100],
        }
    )
    out = delta_notional_split(chain, 50.0)
    assert out is not None
    assert out.call_notional_all == out.call_notional_next
    assert out.put_notional_all == out.put_notional_next
