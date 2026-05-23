"""Fibonacci retracement / extension levels from a daily price series.

Pure transforms over a ``quotes_daily``-style OHLC frame: find the swing high
and low over a lookback window, then derive the standard Fibonacci retracement
levels (between the swing low and high) and downside extension targets. Used as
a descriptive price overlay on the Ticker page — regime context, not a signal
(FlashAlpha rule 4).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

_Swing = tuple[float, float, "date | None", "date | None"]

_RETRACEMENTS = (0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0)
_EXTENSIONS = (1.272, 1.618)


@dataclass(frozen=True)
class FibLevels:
    """Swing anchors plus Fibonacci retracement/extension price levels."""

    high: float
    low: float
    high_date: date | None
    low_date: date | None
    levels: dict[str, float]  # label (e.g. "61.8%") -> price


def swing_high_low(prices: pd.DataFrame, *, lookback: int = 120) -> _Swing | None:
    """Swing high/low (and their dates) over the last ``lookback`` rows.

    Uses ``high``/``low`` columns when present, else ``close``. Returns
    ``(high, low, high_date, low_date)`` or ``None`` if there is no usable data.
    """
    if prices is None or prices.empty:
        return None
    df = prices.tail(lookback).copy()
    high_col = "high" if "high" in df.columns else "close"
    low_col = "low" if "low" in df.columns else "close"
    highs = pd.to_numeric(df[high_col], errors="coerce")
    lows = pd.to_numeric(df[low_col], errors="coerce")
    if highs.dropna().empty or lows.dropna().empty:
        return None
    hi = float(highs.max())
    lo = float(lows.min())
    hi_date = _date_at(df, highs.idxmax())
    lo_date = _date_at(df, lows.idxmin())
    return hi, lo, hi_date, lo_date


def _date_at(df: pd.DataFrame, idx: object) -> date | None:
    if "date" not in df.columns or idx not in df.index:
        return None
    val = df.loc[idx, "date"]
    try:
        ts = pd.Timestamp(val)
    except (ValueError, TypeError):
        return None
    return ts.date() if pd.notna(ts) else None


def fib_levels(prices: pd.DataFrame, *, lookback: int = 120) -> FibLevels | None:
    """Fibonacci retracements + downside extensions over the lookback swing.

    Retracements run from the swing high (0%) down to the swing low (100%);
    extensions (127.2%, 161.8%) project below the low. ``None`` when the swing
    is degenerate (high == low) or there is no data.
    """
    swing = swing_high_low(prices, lookback=lookback)
    if swing is None:
        return None
    hi, lo, hi_date, lo_date = swing
    span = hi - lo
    if span <= 0:
        return None
    levels: dict[str, float] = {}
    for r in _RETRACEMENTS:
        levels[f"{r * 100:.1f}%"] = hi - r * span
    for r in _EXTENSIONS:
        levels[f"{r * 100:.1f}%"] = hi - r * span
    return FibLevels(high=hi, low=lo, high_date=hi_date, low_date=lo_date, levels=levels)
