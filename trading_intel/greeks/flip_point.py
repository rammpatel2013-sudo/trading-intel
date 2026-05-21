"""Gamma flip point (zero-gamma price) via Black-Scholes repricing.

The flip point is the underlying price at which net dealer gamma exposure
crosses zero — below it dealers are typically short gamma (move-amplifying),
above it long gamma (move-dampening). It is a *regime descriptor*, not a
signal (CLAUDE.md rule 4).

Method (chosen 2026-05-21): for each option recompute its Black-Scholes gamma
at a candidate spot ``S`` from the option's strike, IV and time-to-expiry, then
sum the sign-weighted dollar-gamma across the chain and find the zero over
±``search_range`` of spot with ``scipy.optimize.brentq``.

    net_gex(S) = Σ  sign · Γ_BS(S; K, σ, T) · oi · multiplier · S² · 0.01

Returns ``None`` if net gamma does not change sign across the search interval.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
from scipy.optimize import brentq

_SIGN = {"C": 1.0, "P": -1.0}
_DEFAULT_MULTIPLIER = 100.0
_MIN_T = 1.0 / (365.0 * 24.0)  # floor time-to-expiry at ~1 hour to avoid /0
_INV_SQRT_2PI = 1.0 / np.sqrt(2.0 * np.pi)
# Convex returns expiration as days since the Unix epoch (e.g. 20595 = 2026-05-22).
# Anything this large is clearly an epoch-day count, not a days-to-expiry value.
_EPOCH_DAY_THRESHOLD = 10_000


def _norm_pdf(x: np.ndarray) -> np.ndarray:
    return _INV_SQRT_2PI * np.exp(-0.5 * x * x)


def _years_to_expiry(expiration: pd.Series, ref: date) -> np.ndarray:
    """Convert an ``expiration`` column to years-to-expiry.

    Handles: datetimes/date strings (calendar diff), epoch-day integers
    (Convex's native format), and plain days-to-expiry. Floored at ``_MIN_T``.
    """
    ref_ts = pd.Timestamp(ref)
    # Already a datetime (the Convex client normalizes expiration upstream).
    if pd.api.types.is_datetime64_any_dtype(expiration):
        parsed = pd.to_datetime(expiration, errors="coerce")
        years = (parsed - ref_ts).dt.total_seconds().to_numpy() / (365.0 * 24 * 3600)
        return np.maximum(years, _MIN_T)
    numeric = pd.to_numeric(expiration, errors="coerce")
    if numeric.notna().all():
        if float(numeric.median()) >= _EPOCH_DAY_THRESHOLD:
            # Days since Unix epoch → calendar diff in years.
            dates = pd.to_datetime(numeric, unit="D", origin="unix")
            years = (dates - ref_ts).dt.total_seconds().to_numpy() / (365.0 * 24 * 3600)
        else:
            # Plain days-to-expiry.
            years = numeric.to_numpy(dtype=float) / 365.0
    else:
        parsed = pd.to_datetime(expiration, errors="coerce")
        years = (parsed - ref_ts).dt.total_seconds().to_numpy() / (365.0 * 24 * 3600)
    return np.maximum(years, _MIN_T)


def _bs_gamma(spot: float, strike: np.ndarray, sigma: np.ndarray, t: np.ndarray, r: float) -> np.ndarray:
    """Black-Scholes gamma for an array of options at a single spot."""
    sqrt_t = np.sqrt(t)
    d1 = (np.log(spot / strike) + (r + 0.5 * sigma**2) * t) / (sigma * sqrt_t)
    return _norm_pdf(d1) / (spot * sigma * sqrt_t)


def gex_flip(
    chain: pd.DataFrame,
    spot: float,
    *,
    risk_free_rate: float = 0.04,
    search_range: float = 0.10,
) -> float | None:
    """Return the zero-gamma price within ±``search_range`` of ``spot``.

    Needs ``opt_kind, strike, iv, oi, expiration`` columns (``multiplier``
    optional, default 100). Returns ``None`` if no sign change in range or
    insufficient data.
    """
    needed = {"opt_kind", "strike", "iv", "oi", "expiration"}
    if chain is None or chain.empty or not needed.issubset(chain.columns):
        return None
    if not np.isfinite(spot) or spot <= 0:
        return None

    df = chain.copy()
    sign = df["opt_kind"].astype(str).str.upper().str[0].map(_SIGN)
    strike = pd.to_numeric(df["strike"], errors="coerce")
    sigma = pd.to_numeric(df["iv"], errors="coerce")
    oi = pd.to_numeric(df["oi"], errors="coerce")
    if "multiplier" in df.columns:
        mult = pd.to_numeric(df["multiplier"], errors="coerce")
        mult = mult.where(mult > 0, _DEFAULT_MULTIPLIER).fillna(_DEFAULT_MULTIPLIER)
    else:
        mult = pd.Series(_DEFAULT_MULTIPLIER, index=df.index)
    years = _years_to_expiry(df["expiration"], date.today())

    valid = (
        sign.notna()
        & strike.notna() & (strike > 0)
        & sigma.notna() & (sigma > 0)
        & oi.notna()
        & np.isfinite(years)
    )
    if not valid.any():
        return None

    vmask = valid.to_numpy()
    sign_a = sign[valid].to_numpy(dtype=float)
    strike_a = strike[valid].to_numpy(dtype=float)
    sigma_a = sigma[valid].to_numpy(dtype=float)
    oi_a = oi[valid].to_numpy(dtype=float)
    mult_a = mult[valid].to_numpy(dtype=float)
    years_a = years[vmask]
    weight = sign_a * oi_a * mult_a

    def net_gex(price: float) -> float:
        gamma = _bs_gamma(price, strike_a, sigma_a, years_a, risk_free_rate)
        return float(np.sum(weight * gamma) * price**2 * 0.01)

    lo, hi = spot * (1.0 - search_range), spot * (1.0 + search_range)
    g_lo, g_hi = net_gex(lo), net_gex(hi)
    if not (np.isfinite(g_lo) and np.isfinite(g_hi)) or g_lo * g_hi > 0:
        return None  # no sign change in the bracket → no flip point in range

    return float(brentq(net_gex, lo, hi, xtol=1e-2, maxiter=100))
