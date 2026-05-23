"""Realized-volatility from a daily close series.

Annualized close-to-close realized vol over rolling windows. Pure transforms (no
I/O) so they are trivially testable and reusable: the quotes collector fills
``quotes_daily.rv20`` / ``rv60`` from these, and the GEX:RVOL regime ratio
(MEMORY.md: primary regime classifier) will consume rv20 once it is wired.

Regime descriptor only (FlashAlpha rule 4) — no signals.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

_TRADING_DAYS = 252


def log_returns(close: pd.Series) -> pd.Series:
    """Daily log returns ``ln(close_t / close_{t-1})`` (first value NaN)."""
    close = pd.to_numeric(close, errors="coerce")
    return np.log(close / close.shift(1))


def realized_vol(
    close: pd.Series, window: int, *, trading_days: int = _TRADING_DAYS
) -> pd.Series:
    """Annualized rolling realized vol (decimal) over ``window`` sessions.

    Sample stdev (``ddof=1``) of daily log returns, scaled by
    ``sqrt(trading_days)``. NaN until ``window`` returns are available.
    """
    returns = log_returns(close)
    return returns.rolling(window=window, min_periods=window).std(ddof=1) * np.sqrt(
        trading_days
    )


def add_realized_vol(
    df: pd.DataFrame,
    *,
    close_col: str = "close",
    windows: tuple[int, ...] = (20, 60),
    trading_days: int = _TRADING_DAYS,
) -> pd.DataFrame:
    """Return ``df`` with an ``rv{w}`` column per window (e.g. ``rv20``/``rv60``).

    Assumes ``df`` is ordered oldest-first. Empty / column-less input is returned
    unchanged (with the rv columns added as empty when possible).
    """
    out = df.copy()
    if close_col not in out.columns:
        for w in windows:
            out[f"rv{w}"] = pd.Series(dtype=float)
        return out
    for w in windows:
        out[f"rv{w}"] = realized_vol(out[close_col], w, trading_days=trading_days)
    return out
