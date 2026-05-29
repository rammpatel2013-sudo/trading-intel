"""VIX beta — single-name ATM-IV sensitivity to VIX moves.

Per ADR-003 §3.3: regress 60d of ``Δiv_atm`` on ``Δvix`` for a single name to
get its VIX beta, the natural scale for normalizing a single-name skew read
against index-level skew. A name with ``vix_beta = 1.5`` should see its ATM IV
move ~50% more than the VIX on any given session; the abnormal residual is
``Δrr - β·ΔSDEX`` (computed in the row builder, not here).

Pure numpy / pandas — no DB, no client. The job layer supplies the daily series.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

#: Default rolling window in trading days.
DEFAULT_WINDOW = 60

#: Minimum non-missing pairs required for a meaningful OLS fit.
DEFAULT_MIN_OBS = 40


def _aligned_diffs(
    iv_series: pd.Series, vix_series: pd.Series, *, window: int
) -> tuple[np.ndarray, np.ndarray]:
    """First-difference both series, align on the inner intersection of dates.

    Returns ``(d_iv, d_vix)`` as 1-d float arrays trimmed to the last
    ``window`` aligned observations (or fewer if history is short). Drops any
    pair whose IV-diff or VIX-diff is NaN.
    """
    if iv_series is None or vix_series is None:
        return np.array([]), np.array([])
    iv = pd.to_numeric(iv_series, errors="coerce").dropna().sort_index()
    vix = pd.to_numeric(vix_series, errors="coerce").dropna().sort_index()
    if iv.empty or vix.empty:
        return np.array([]), np.array([])
    df = pd.concat([iv.rename("iv"), vix.rename("vix")], axis=1, join="inner")
    if df.empty:
        return np.array([]), np.array([])
    d = df.diff().dropna()
    if d.empty:
        return np.array([]), np.array([])
    d = d.tail(window)
    return d["iv"].to_numpy(dtype=float), d["vix"].to_numpy(dtype=float)


def vix_beta(
    iv_series: pd.Series,
    vix_series: pd.Series,
    *,
    window: int = DEFAULT_WINDOW,
    min_obs: int = DEFAULT_MIN_OBS,
) -> float | None:
    """Rolling-window OLS β of ``Δiv`` on ``Δvix``. ``None`` if cold or degenerate.

    Both series are pandas Series indexed by date (or any sortable index). The
    function aligns them on the inner intersection, takes first differences,
    keeps the last ``window`` aligned points, and fits ``Δiv = a + β·Δvix + ε``.
    """
    d_iv, d_vix = _aligned_diffs(iv_series, vix_series, window=window)
    if d_iv.size < min_obs:
        return None
    var_vix = float(d_vix.var(ddof=1))
    if not np.isfinite(var_vix) or var_vix <= 0:
        return None
    cov = float(np.cov(d_iv, d_vix, ddof=1)[0, 1])
    if not np.isfinite(cov):
        return None
    return cov / var_vix


def abnormal_rr_change(
    *,
    d_rr_name: float | None,
    d_index_skew: float | None,
    beta: float | None,
) -> float | None:
    """Residual ``Δrr_i,t - β_i · Δindex_skew_t`` for one name on one day.

    Inputs are all in vol points. ``None`` if any leg is unavailable — the row
    leaves ``rr_25d_abnormal`` NULL rather than imputing zero.
    """
    if d_rr_name is None or d_index_skew is None or beta is None:
        return None
    if not (np.isfinite(d_rr_name) and np.isfinite(d_index_skew) and np.isfinite(beta)):
        return None
    return float(d_rr_name) - float(beta) * float(d_index_skew)
