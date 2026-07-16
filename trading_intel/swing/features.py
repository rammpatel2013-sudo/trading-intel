"""Pure swing feature math (realized vol, 25d skew).

The vendor-agnostic feature computations shared by ``scripts/swing_report.py``
and the ``swing_features`` collector (P3 extraction). The LIVE CVForge pulls
(chain, exposures, RSI/SMA) stay in the collector/report edge; only the pure math
lives here so it is unit-tested without a vendor.

Descriptive features only (FlashAlpha rule 4).
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd


def realized_vol(closes: np.ndarray, window: int = 20) -> float | None:
    """Annualized close-to-close realized vol over the last ``window`` returns.

    ``None`` when there are fewer than ``window + 1`` closes (can't form the
    window). Uses sample std (ddof=1) x sqrt252, matching the report + collector.
    """
    closes = np.asarray(closes, dtype=float)
    if closes.size < window + 1:
        return None
    rets = np.diff(np.log(closes[-(window + 1) :]))
    return float(rets.std(ddof=1) * np.sqrt(252))


def skew_25d(
    chain: pd.DataFrame, *, ref: date | None = None, dte_lo: int = 25, dte_hi: int = 60
) -> float | None:
    """25d put IV - 25d call IV on the nearest expiry in the DTE window.

    Positive = put skew (the equity norm). Needs ``delta``, ``iv``, ``expiration``,
    ``opt_kind`` columns. ``ref`` anchors the DTE window (defaults to today).
    ``None`` when the chain lacks a usable expiry or a wing in the window.
    """
    if chain is None or chain.empty or "delta" not in chain.columns:
        return None
    ref = ref or date.today()
    df = chain.dropna(subset=["delta", "iv", "expiration"]).copy()
    if df.empty:
        return None
    dte = (df["expiration"] - pd.Timestamp(ref)).dt.days
    df = df[(dte >= dte_lo) & (dte <= dte_hi)]
    if df.empty:
        return None
    target = df.loc[(df["expiration"] - df["expiration"].min()).abs().idxmin(), "expiration"]
    df = df[df["expiration"] == target]
    calls = df[df["opt_kind"].astype(str).str.upper().str[0] == "C"]
    puts = df[df["opt_kind"].astype(str).str.upper().str[0] == "P"]
    if calls.empty or puts.empty:
        return None
    c = calls.iloc[(calls["delta"] - 0.25).abs().argmin()]
    p = puts.iloc[(puts["delta"] + 0.25).abs().argmin()]
    return float(p["iv"] - c["iv"])


def iv_rv_ratio(atm_iv: float | None, rv: float | None) -> float | None:
    """ATM IV ÷ realized vol; ``None`` if either is missing or RV is zero."""
    if atm_iv is None or not rv:
        return None
    return atm_iv / rv
