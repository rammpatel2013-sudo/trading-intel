"""Price a defined-risk call spread off the CVForge chain.

The normalized CVForge chain carries per-contract ``iv`` (plus spot, strike, expiry)
but no option premium, so — exactly as ``greeks/straddle.py`` does — we Black-Scholes
price each leg from its own stored IV. IV round-trips to the market mark, so a BS price
off it is the real option mark. That fills the ⚡ structure's MAX-RISK / TARGET numbers
from CVForge's own chain data. Pure (DataFrame-in / prices-out) and unit-tested;
degrades to ``None`` legs when the chain lacks a usable match. Descriptive only (rule 4).
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from trading_intel.greeks.black_scholes import bs_call_price, years_to_expiry

_MONTHS = {
    m: i
    for i, m in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
    )
}


def _call_rows(chain_df: pd.DataFrame) -> pd.DataFrame:
    cp = chain_df["opt_kind"].astype(str).str.upper().str[0]
    df = chain_df[
        (cp == "C")
        & chain_df["expiration"].notna()
        & chain_df["strike"].notna()
        & chain_df["iv"].notna()
    ].copy()
    df["strike"] = df["strike"].astype(float)
    df["iv"] = df["iv"].astype(float)
    return df


def _leg_price(calls: pd.DataFrame, month_num: int, strike: float, ref: date) -> float | None:
    """BS price of the call at ``strike`` in month ``month_num`` (max-OI expiry, near strike)."""
    df = calls[pd.to_datetime(calls["expiration"]).dt.month == month_num]
    if df.empty:
        return None
    df = df.assign(_sd=(df["strike"] - strike).abs())
    tol = max(2.5, strike * 0.03)
    df = df[df["_sd"] <= tol]
    if df.empty:
        return None
    # nearest strike, then the deepest-OI expiry at that strike (the monthly, usually)
    best_sd = df["_sd"].min()
    df = df[df["_sd"] <= best_sd + 1e-9]
    if "oi" in df.columns and df["oi"].notna().any():
        row = df.sort_values("oi", ascending=False).iloc[0]
    else:
        row = df.iloc[0]
    t = float(years_to_expiry(pd.Series([row["expiration"]]), ref)[0])
    spot = float(row["underlying_price"])
    sigma = float(row["iv"])
    if sigma <= 0 or spot <= 0:
        return None
    return round(float(bs_call_price(spot, float(row["strike"]), sigma, t)), 2)


def price_call_spread(
    chain_df: pd.DataFrame | None,
    month: str,
    long_strike: float,
    short_strike: float,
    *,
    ref_date: date | None = None,
) -> dict[str, Any]:
    """``{long_price, short_price}`` for the ``long/short`` call spread, or ``None`` legs.

    ``month`` is a name/abbrev ("December"/"Sep"); legs price from each strike's stored
    IV in the CVForge chain. Missing chain / no matching contracts → ``None`` legs, so
    the structure still renders with "live-priced" instead of numbers.
    """
    empty = {"long_price": None, "short_price": None}
    if chain_df is None or getattr(chain_df, "empty", True):
        return empty
    month_num = _MONTHS.get(str(month)[:3].lower())
    if not month_num:
        return empty
    try:
        calls = _call_rows(chain_df)
    except (KeyError, ValueError, TypeError):
        return empty
    if calls.empty:
        return empty
    ref = ref_date or date.today()
    return {
        "long_price": _leg_price(calls, month_num, float(long_strike), ref),
        "short_price": _leg_price(calls, month_num, float(short_strike), ref),
    }
