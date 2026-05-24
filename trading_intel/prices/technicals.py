"""Price-based technical indicators (pure transforms).

Currently Wilder's RSI. Pure pandas, no I/O - feeds the charting page alongside
realized vol. Descriptive indicators only (FlashAlpha rule 4).
"""
from __future__ import annotations

import pandas as pd


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI over ``period`` (0-100). NaN until ``period`` deltas exist.

    Uses Wilder smoothing (an EWM with alpha = 1/period). When average loss is
    zero (all gains) RSI is 100; the series is NaN where there isn't enough data.
    """
    c = pd.to_numeric(close, errors="coerce")
    delta = c.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)
