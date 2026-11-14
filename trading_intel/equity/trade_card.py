"""Trade card pieces from a CVForge option chain (E5). Pure (pandas only to read the chain).

* ``call_iv_row``    - 90-180 DTE ATM and 25-delta call IV, call volume / OI -> ``equity_call_iv``
                       (banked daily for the shortlist; the coiled-spring "calls gaining value while
                       the stock sits still" check needs this history, so it builds from day one)
* ``call_bid``       - that check: 25-delta call IV up >= 2 vol points over ~10 sessions while the
                       stock moved less than 3% either way
* ``debit_spread``   - Track A structure (swing-trade-system-build, locked 2026-07-15): buy ~70-delta
                       call / sell ~40-delta call, expiry nearest 120 days inside 90-180. Prices are
                       MODEL prices from the chain's IV (the chain carries no quotes) - check the live
                       bid/ask before trading.
* ``HEDGE_MENU``     - the three hedges on the equity REMIND list, described, NOT ranked: the SPY
                       2018/2020/2022 backtest that would rank them is still queued.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date

import numpy as np
import pandas as pd

from trading_intel.greeks.black_scholes import bs_call_price, norm_cdf

RATE = 0.04


def pick_expiry(
    chain: pd.DataFrame, today: date, *, lo: int = 90, hi: int = 180, target: int = 120
) -> pd.Timestamp | None:
    if chain is None or chain.empty:
        return None
    exps = pd.to_datetime(chain["expiration"]).dt.normalize().drop_duplicates()
    dte = (exps - pd.Timestamp(today)).dt.days
    ok = exps[(dte >= lo) & (dte <= hi)]
    if ok.empty:
        return None
    return ok.iloc[int(np.argmin(np.abs((ok - pd.Timestamp(today)).dt.days - target)))]


def _calls(chain: pd.DataFrame, exp: pd.Timestamp) -> pd.DataFrame:
    c = chain[
        (pd.to_datetime(chain["expiration"]).dt.normalize() == exp)
        & (chain["opt_kind"].str.lower().str.startswith("c"))
    ]
    return c.dropna(subset=["strike", "iv", "delta"]).sort_values("strike")


def call_iv_row(chain: pd.DataFrame, symbol: str, today: date) -> dict | None:
    exp = pick_expiry(chain, today)
    if exp is None:
        return None
    calls = _calls(chain, exp)
    if calls.empty:
        return None
    spot = float(chain["underlying_price"].iloc[0])
    atm = calls.iloc[int(np.argmin(np.abs(calls["strike"].to_numpy() - spot)))]
    c25 = calls.iloc[int(np.argmin(np.abs(calls["delta"].to_numpy() - 0.25)))]
    otm = calls[calls["strike"] >= spot]
    return {
        "date": today,
        "symbol": symbol,
        "expiry": exp.date(),
        "dte": int((exp - pd.Timestamp(today)).days),
        "spot": spot,
        "iv_atm": float(atm["iv"]),
        "iv_call_25d": float(c25["iv"]),
        "call_volume": float(otm["volume"].fillna(0).sum()),
        "call_oi": float(otm["oi"].fillna(0).sum()),
    }


def call_bid(
    iv_hist: Sequence[Mapping], *, lookback: int = 10, iv_up: float = 0.02, max_move: float = 0.03
) -> dict | None:
    """``iv_hist`` = equity_call_iv rows for one name, oldest first."""
    rows = [r for r in iv_hist if r.get("iv_call_25d") and r.get("spot")]
    if len(rows) < lookback // 2 + 1:
        return {"ready": False, "sessions": len(rows)}
    a, b = rows[max(0, len(rows) - 1 - lookback)], rows[-1]
    d_iv = b["iv_call_25d"] - a["iv_call_25d"]
    move = b["spot"] / a["spot"] - 1.0
    return {
        "ready": True,
        "sessions": len(rows),
        "iv_change": d_iv,
        "price_change": move,
        "call_bid": bool(d_iv >= iv_up and abs(move) <= max_move),
    }


def debit_spread(
    chain: pd.DataFrame, today: date, *, long_delta: float = 0.70, short_delta: float = 0.40
) -> dict | None:
    exp = pick_expiry(chain, today)
    if exp is None:
        return None
    calls = _calls(chain, exp)
    if len(calls) < 4:
        return None
    spot = float(chain["underlying_price"].iloc[0])
    lg = calls.iloc[int(np.argmin(np.abs(calls["delta"].to_numpy() - long_delta)))]
    sh_pool = calls[calls["strike"] > lg["strike"]]
    if sh_pool.empty:
        return None
    sh = sh_pool.iloc[int(np.argmin(np.abs(sh_pool["delta"].to_numpy() - short_delta)))]
    t = max((exp - pd.Timestamp(today)).days, 1) / 365.0
    p_long = float(
        bs_call_price(spot, np.array([lg["strike"]]), np.array([lg["iv"]]), np.array([t]), RATE)[0]
    )
    p_short = float(
        bs_call_price(spot, np.array([sh["strike"]]), np.array([sh["iv"]]), np.array([t]), RATE)[0]
    )
    debit = p_long - p_short
    width = float(sh["strike"] - lg["strike"])
    if debit <= 0 or width <= 0:
        return None
    be = float(lg["strike"]) + debit
    sig = float(lg["iv"])
    d2 = (np.log(spot / be) + (RATE - 0.5 * sig**2) * t) / (sig * np.sqrt(t))
    return {
        "expiry": exp.date(),
        "dte": int((exp - pd.Timestamp(today)).days),
        "spot": spot,
        "long_strike": float(lg["strike"]),
        "long_delta": float(lg["delta"]),
        "short_strike": float(sh["strike"]),
        "short_delta": float(sh["delta"]),
        "debit": round(debit, 2),
        "width": width,
        "max_loss": round(debit * 100, 0),
        "max_gain": round((width - debit) * 100, 0),
        "breakeven": round(be, 2),
        "reward_risk": round((width - debit) / debit, 2),
        "pop": round(float(norm_cdf(np.array([d2]))[0]), 2),
        "priced_by": "model (chain IV)",
    }


HEDGE_MENU = (
    (
        "No hedge",
        "Costs nothing. Fine while the gate is green; the stop on each name is the protection.",
    ),
    (
        "Plain SPY put (or put spread)",
        "Pays from the first point down; costs the premium every month "
        "whether or not the drop comes.",
    ),
    (
        "SPY put ratio backspread",
        "Sell 1 nearer put, buy 2 further-out puts: little or no cost, pays in a "
        "real crash, loses most in a slow grind down to the sold strike.",
    ),
)
HEDGE_RULE = (
    "Standing rule from the equity list: no naked short puts and no put front-ratios on names "
    "you hold. Which hedge is best is NOT tested yet (SPY 2018/2020/2022 backtest queued)."
)
