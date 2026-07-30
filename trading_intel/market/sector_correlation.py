"""Sector-ETF correlation regime — rolling AVERAGE PAIRWISE correlation + dispersion.

Realized correlation off the daily returns of the 11 sector SPDRs. Complements the
option-implied CBOE COR1M/COR3M already in the VIX complex (that's what options are
pricing; this is what sectors are actually doing). Descriptor only (FlashAlpha
rule 4) — nothing here emits a signal.

Read: high avg pairwise correlation (>~0.7) → sectors move together, barbells/pairs
don't diversify; low (<~0.4) → dispersion regime, pair trades and barbells work.

Pure transform (a wide close-price frame in, numbers out) so it's unit-testable and
has no vendor/DB dependency. Prices come from the existing yfinance ``quotes_daily``
(free) — no IBKR.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# The 11 SPDR Select Sector ETFs (add these to the quotes universe).
SECTOR_SPDRS: tuple[str, ...] = (
    "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY",
)

# Regime thresholds (proposal): >0.7 high, <0.4 low; alert bands at 0.65 / 0.35.
_HIGH, _LOW = 0.70, 0.40


def compute_returns(prices: pd.DataFrame) -> pd.DataFrame:
    """Daily simple returns from a wide close-price frame (index=date, cols=tickers)."""
    return prices.sort_index().pct_change()


def avg_pairwise_corr(returns: pd.DataFrame, window: int, *, min_periods: int | None = None) -> pd.Series:
    """Rolling mean of the OFF-DIAGONAL pairwise correlations (excludes self-corr = 1).

    This is the fix for the common bug of averaging the full matrix (its diagonal of
    ones inflates the number). Uses the mean of the upper triangle of each window's
    correlation matrix. ``pandas.corr`` handles pairwise-complete observations, so an
    occasional missing close doesn't drop the whole window.
    """
    n = returns.shape[1]
    out = pd.Series(index=returns.index, dtype=float)
    if n < 2:
        return out
    mp = min_periods or window
    iu = np.triu_indices(n, k=1)  # off-diagonal (upper triangle) index
    for i in range(len(returns)):
        if i + 1 < mp:
            continue
        win = returns.iloc[max(0, i - window + 1) : i + 1]
        if win.shape[0] < mp:
            continue
        c = win.corr().to_numpy()
        if c.shape == (n, n):
            out.iloc[i] = float(np.nanmean(c[iu]))
    return out


def cross_sectional_dispersion(returns: pd.DataFrame) -> pd.Series:
    """Per-day dispersion = cross-sectional stdev of that day's sector returns."""
    return returns.std(axis=1, ddof=0)


def corr_regime(avg_corr: float | None) -> str:
    """Label the correlation regime (descriptor only)."""
    if avg_corr is None or not np.isfinite(avg_corr):
        return "n/a"
    if avg_corr > _HIGH:
        return "high — sectors moving together (barbells don't diversify)"
    if avg_corr < _LOW:
        return "low — dispersion regime (pair trades / barbells work)"
    return "normal"


def latest_snapshot(prices: pd.DataFrame, *, windows: tuple[int, ...] = (21, 63)) -> dict:
    """Latest correlation-regime snapshot for storage / the report.

    Returns ``{as_of, avg_corr{"21d","63d"}, regime{...}, dispersion, matrix, n_etfs}``.
    ``matrix`` is the longest-window pairwise correlation matrix (nested dict) for the
    heat-map. ``None`` fields where there isn't enough history yet.
    """
    returns = compute_returns(prices).dropna(how="all")
    snap: dict = {
        "as_of": None, "avg_corr": {}, "regime": {}, "dispersion": None,
        "matrix": None, "n_etfs": int(prices.shape[1]),
    }
    if returns.empty:
        return snap
    last = returns.index[-1]
    snap["as_of"] = str(last.date() if hasattr(last, "date") else last)
    for w in windows:
        s = avg_pairwise_corr(returns, w)
        v = float(s.iloc[-1]) if len(s) and np.isfinite(s.iloc[-1]) else None
        snap["avg_corr"][f"{w}d"] = v
        snap["regime"][f"{w}d"] = corr_regime(v)
    disp = cross_sectional_dispersion(returns)
    snap["dispersion"] = float(disp.iloc[-1]) if len(disp) and np.isfinite(disp.iloc[-1]) else None
    wmax = max(windows)
    win = returns.iloc[-wmax:]
    if win.shape[0] >= 2:
        snap["matrix"] = win.corr().round(4).to_dict()
    return snap
