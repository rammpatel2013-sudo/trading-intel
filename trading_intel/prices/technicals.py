"""Price-based technical indicators (pure transforms).

Wilder's RSI is hand-rolled (no dependency); the heavier indicators (MACD,
Bollinger, ATR) delegate to the ``ta`` library, imported lazily so a missing
install degrades to a clear error at call time instead of breaking the MCP
server at import. Candlestick-pattern detection is pure pandas. No I/O here -
these feed the charting page and the MCP technicals tool. Descriptive
indicators only (FlashAlpha rule 4).
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


def sma(close: pd.Series, period: int = 20) -> pd.Series:
    """Simple moving average over ``period`` (pure pandas)."""
    return pd.to_numeric(close, errors="coerce").rolling(period, min_periods=period).mean()


def ema(close: pd.Series, period: int = 20) -> pd.Series:
    """Exponential moving average over ``period`` (pure pandas)."""
    return pd.to_numeric(close, errors="coerce").ewm(span=period, adjust=False).mean()


def macd(
    close: pd.Series, *, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.DataFrame:
    """MACD line, signal line and histogram via the ``ta`` library.

    Columns: ``macd, signal, hist``. Raises ``ImportError`` (with install hint)
    if ``ta`` is not installed.
    """
    ta_trend = _import_ta("trend")
    ind = ta_trend.MACD(
        close=pd.to_numeric(close, errors="coerce"),
        window_slow=slow, window_fast=fast, window_sign=signal,
    )
    return pd.DataFrame(
        {"macd": ind.macd(), "signal": ind.macd_signal(), "hist": ind.macd_diff()}
    )


def bollinger(close: pd.Series, *, period: int = 20, k: float = 2.0) -> pd.DataFrame:
    """Bollinger bands via the ``ta`` library.

    Columns: ``mid, upper, lower, pctb`` (%B position of price within the band).
    """
    ta_vol = _import_ta("volatility")
    bb = ta_vol.BollingerBands(
        close=pd.to_numeric(close, errors="coerce"), window=period, window_dev=k
    )
    return pd.DataFrame(
        {
            "mid": bb.bollinger_mavg(),
            "upper": bb.bollinger_hband(),
            "lower": bb.bollinger_lband(),
            "pctb": bb.bollinger_pband(),
        }
    )


def atr(
    high: pd.Series, low: pd.Series, close: pd.Series, *, period: int = 14
) -> pd.Series:
    """Average True Range via the ``ta`` library."""
    ta_vol = _import_ta("volatility")
    return ta_vol.AverageTrueRange(
        high=pd.to_numeric(high, errors="coerce"),
        low=pd.to_numeric(low, errors="coerce"),
        close=pd.to_numeric(close, errors="coerce"),
        window=period,
    ).average_true_range()


_PATTERN_DOC_THRESH = 0.1  # body <= 10% of range -> doji
_PATTERN_WICK_RATIO = 2.0  # long wick >= 2x body -> hammer / star


def candlestick_patterns(df: pd.DataFrame) -> dict[str, bool]:
    """Detect common single/two-bar candlestick patterns on the latest bar.

    Pure pandas (no ``ta`` dependency). ``df`` must have ``open, high, low,
    close`` columns, oldest-first. Returns a flag per pattern for the most
    recent bar. Descriptive only - rule 4 (a pattern is not a signal).
    """
    cols = {"open", "high", "low", "close"}
    if df is None or df.empty or not cols.issubset(df.columns):
        return {}
    o = float(df["open"].iloc[-1])
    h = float(df["high"].iloc[-1])
    low_ = float(df["low"].iloc[-1])
    c = float(df["close"].iloc[-1])
    rng = h - low_
    if rng <= 0:
        return {}
    body = abs(c - o)
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - low_
    bull = c > o

    out = {
        "doji": body <= _PATTERN_DOC_THRESH * rng,
        "hammer": (
            lower_wick >= _PATTERN_WICK_RATIO * body and upper_wick <= body
        ),
        "shooting_star": (
            upper_wick >= _PATTERN_WICK_RATIO * body and lower_wick <= body
        ),
        "marubozu": body >= 0.9 * rng,
    }
    if len(df) >= 2:
        po = float(df["open"].iloc[-2])
        pc = float(df["close"].iloc[-2])
        out["bullish_engulfing"] = bull and pc < po and c >= po and o <= pc
        out["bearish_engulfing"] = (not bull) and pc > po and o >= pc and c <= po
    return {k: bool(v) for k, v in out.items()}


def _import_ta(submodule: str):  # noqa: ANN201 (returns the ta submodule)
    """Lazily import a ``ta`` submodule with a friendly install hint."""
    try:
        module = __import__(f"ta.{submodule}", fromlist=[submodule])
    except ImportError as exc:  # pragma: no cover - exercised only when ta absent
        raise ImportError(
            "the 'ta' library is required for this indicator; "
            "install it with: pip install ta"
        ) from exc
    return module
