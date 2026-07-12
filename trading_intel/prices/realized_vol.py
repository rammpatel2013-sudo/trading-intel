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


def realized_vol(close: pd.Series, window: int, *, trading_days: int = _TRADING_DAYS) -> pd.Series:
    """Annualized rolling realized vol (decimal) over ``window`` sessions.

    Sample stdev (``ddof=1``) of daily log returns, scaled by
    ``sqrt(trading_days)``. NaN until ``window`` returns are available.
    """
    returns = log_returns(close)
    return returns.rolling(window=window, min_periods=window).std(ddof=1) * np.sqrt(trading_days)


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


def rv_rolloff_projection(
    close: pd.Series,
    *,
    window: int = 21,
    horizon: int = 10,
    future_return: float = 0.0,
    trading_days: int = _TRADING_DAYS,
) -> pd.DataFrame:
    """Project the mechanical drift of trailing-``window`` realized vol.

    Holds the window width fixed and rolls it forward ``horizon`` sessions: on
    each future day the oldest past log-return leaves the window and one
    ``future_return`` (default 0.0 — a calm-tape assumption) enters. As large
    past returns age out, measured RV mechanically declines even if nothing
    happens on the tape — Doc McGraw's "the big June down-days age out of the
    21-day window ~mid-July, dragging measured vol toward a floor, then the floor
    becomes a launchpad" mechanic. Useful for anticipating systematic-flow
    (vol-target / CTA) buying pressure that keys off falling realized vol.

    Returns a DataFrame ordered by ``session_offset`` 0..``horizon`` with:
      - ``session_offset``  : sessions ahead (0 = today's trailing-window RV)
      - ``projected_rv``    : annualized RV (decimal) of the rolled window
      - ``dropped_return``  : the past log-return that left the window that day
                              (NaN at offset 0; magnitude flags the "cliff")

    Pure transform, no I/O. Regime descriptor only (FlashAlpha rule 4) — the
    projection is mechanical accounting, not a directional signal. Assumes at
    least ``window`` returns of history for a clean read; degrades to the
    available window otherwise.
    """
    horizon = max(0, int(horizon))
    returns = log_returns(pd.to_numeric(close, errors="coerce")).dropna().to_numpy()
    base = returns[-window:] if returns.size >= window else returns
    n = base.size
    scale = float(np.sqrt(trading_days))
    rows: list[dict[str, float | int | None]] = []
    for k in range(horizon + 1):
        kept = base[k:] if k <= n else base[n:]
        projected = np.concatenate([kept, np.full(k, float(future_return))])
        if projected.size >= 2:
            rv: float = float(np.std(projected, ddof=1) * scale)
        else:
            rv = float("nan")
        dropped = float(base[k - 1]) if 1 <= k <= n else None
        rows.append({"session_offset": k, "projected_rv": rv, "dropped_return": dropped})
    return pd.DataFrame(rows)
