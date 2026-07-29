"""Call-spread pricing off a CVForge-shaped chain — pure BS, no network."""

from __future__ import annotations

from datetime import date

import pandas as pd

from trading_intel.jaguar.pricing import price_call_spread

REF = date(2026, 7, 29)


def _chain() -> pd.DataFrame:
    exp = pd.Timestamp("2026-12-18")
    return pd.DataFrame(
        [
            {
                "opt_kind": "call",
                "strike": 50.0,
                "iv": 0.35,
                "expiration": exp,
                "underlying_price": 50.0,
                "oi": 1000,
            },
            {
                "opt_kind": "call",
                "strike": 60.0,
                "iv": 0.35,
                "expiration": exp,
                "underlying_price": 50.0,
                "oi": 500,
            },
            {
                "opt_kind": "put",
                "strike": 50.0,
                "iv": 0.40,
                "expiration": exp,
                "underlying_price": 50.0,
                "oi": 100,
            },
        ]
    )


def test_prices_spread_from_iv():
    m = price_call_spread(_chain(), "December", 50, 60, ref_date=REF)
    assert m["long_price"] is not None and m["short_price"] is not None
    assert m["long_price"] > m["short_price"] > 0  # ATM-50 richer than OTM-60
    debit = m["long_price"] - m["short_price"]
    assert 0 < debit < 10  # inside the 10-wide spread → a valid defined-risk debit


def test_missing_or_empty_chain_degrades():
    none = {"long_price": None, "short_price": None}
    assert price_call_spread(None, "Dec", 50, 60) == none
    assert price_call_spread(pd.DataFrame(), "Dec", 50, 60) == none


def test_wrong_month_has_no_match():
    m = price_call_spread(_chain(), "September", 50, 60, ref_date=REF)
    assert m == {"long_price": None, "short_price": None}
