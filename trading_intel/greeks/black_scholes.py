"""Shared Black-Scholes greeks for simulation / what-if views (ADR-002).

Convex pre-computed greeks (gxoi/dxoi/...) remain the DEFAULT for snapshot and
by-strike views. This module is used ONLY by views that synthetically shock spot
or IV — the gamma-flip repricing (``flip_point.py``) and the spot-ladder MM
gamma profile (``gamma_profile.py``) — plus, as of ADR-004, to synthesize the
``vanna``/``charm`` columns for a first-order-only vendor (CVForge). Keeping the
BS math in one place avoids scattered ad-hoc pricing (ADR-002).

Conventions match the flip-point method documented in MEMORY (2026-05-21):

    dollar_gamma(S) = sign * gamma_BS(S; K, sigma, T) * oi * multiplier * S^2 * 0.01

i.e. dealer dollar-gamma per 1% move, calls +, puts - (the project's sign
convention). Sticky-strike is the caller's responsibility: pass each strike's
own stored IV and it stays fixed as spot moves.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
from scipy.special import ndtr

_INV_SQRT_2PI = 1.0 / np.sqrt(2.0 * np.pi)
_MIN_T = 1.0 / (365.0 * 24.0)  # floor time-to-expiry at ~1 hour to avoid /0
# Convex returns expiration as days since the Unix epoch (e.g. 20595 = 2026-05-22).
# Anything this large is clearly an epoch-day count, not a days-to-expiry value.
_EPOCH_DAY_THRESHOLD = 10_000

__all__ = [
    "bs_call_price",
    "bs_charm",
    "bs_gamma",
    "bs_put_price",
    "bs_vanna",
    "dollar_gamma",
    "norm_cdf",
    "norm_pdf",
    "years_to_expiry",
]


def norm_pdf(x: np.ndarray) -> np.ndarray:
    """Standard normal PDF."""
    return _INV_SQRT_2PI * np.exp(-0.5 * x * x)


def norm_cdf(x: np.ndarray) -> np.ndarray:
    """Standard normal CDF (Phi), vectorized via ``scipy.special.ndtr``.

    Uses the same scipy dependency the flip-point solver (``flip_point.py``)
    already relies on. Needed to price the ATM straddle (``greeks/straddle.py``).
    """
    return ndtr(x)


def bs_gamma(
    spot: float | np.ndarray,
    strike: np.ndarray,
    sigma: np.ndarray,
    t: np.ndarray,
    r: float = 0.0,
) -> np.ndarray:
    """Black-Scholes gamma for arrays of options (broadcasts against ``spot``).

    Gamma is identical for calls and puts; the caller applies the dealer sign.
    """
    sqrt_t = np.sqrt(t)
    d1 = (np.log(spot / strike) + (r + 0.5 * sigma**2) * t) / (sigma * sqrt_t)
    return norm_pdf(d1) / (spot * sigma * sqrt_t)


def bs_charm(
    spot: float | np.ndarray,
    strike: np.ndarray,
    sigma: np.ndarray,
    t: np.ndarray,
    r: float = 0.0,
) -> np.ndarray:
    """Black-Scholes charm (∂Δ/∂t) for arrays of options, no-dividends.

    ``charm = -N'(d1) * [r/(σ√T) - d2/(2T)]`` — calendar-time derivative of
    delta, expressed per year. With no dividends, the value is identical for
    calls and puts (Δ_put = Δ_call - 1, the constant drops out under
    differentiation), so the caller applies the dealer sign (calls +, puts -)
    when aggregating into hedging-flow exposure.

    Matches the convention used by ``bs_gamma`` in this module: broadcasts
    against ``spot``, returns a numpy array of the same shape as ``strike``.
    """
    sqrt_t = np.sqrt(t)
    d1 = (np.log(spot / strike) + (r + 0.5 * sigma**2) * t) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    # Per-year charm: -N'(d1) * (r/(σ√T) - d2/(2T)). The bracketed term grows
    # as 1/√T near expiry; that's the well-known "charm explodes ATM into the
    # close" behaviour (MEMORY note on charm intuition).
    return -norm_pdf(d1) * (r / (sigma * sqrt_t) - d2 / (2.0 * t))


def bs_vanna(
    spot: float | np.ndarray,
    strike: np.ndarray,
    sigma: np.ndarray,
    t: np.ndarray,
    r: float = 0.0,
) -> np.ndarray:
    """Black-Scholes vanna (∂Δ/∂σ = ∂vega/∂S) for arrays of options, no-dividends.

    ``vanna = -N'(d1) * d2 / σ`` — the cross-partial of value w.r.t. spot and
    vol, expressed per 1.00 of vol (per unit sigma, NOT per vol-point). Identical
    for calls and puts (Δ_put = Δ_call − 1, the constant drops under
    differentiation), so the caller applies the dealer sign when aggregating into
    vanna-hedging exposure.

    Used to synthesize the ``vanna`` column for vendors that ship first-order
    greeks only (e.g. CVForge), so ``exposures.compute_exposures`` stays
    vendor-agnostic (ADR-002 precedent, extended in ADR-004). Matches
    ``bs_gamma``/``bs_charm`` here: broadcasts against ``spot``, returns a numpy
    array shaped like ``strike``. Validated three ways (analytic = vega-identity =
    finite-difference) against a live SPY contract, 2026-07-12.
    """
    sqrt_t = np.sqrt(t)
    d1 = (np.log(spot / strike) + (r + 0.5 * sigma**2) * t) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    return -norm_pdf(d1) * d2 / sigma


def bs_call_price(
    spot: float | np.ndarray,
    strike: np.ndarray,
    sigma: np.ndarray,
    t: np.ndarray,
    r: float = 0.0,
) -> np.ndarray:
    """Black-Scholes call price, no dividends; broadcasts against ``spot``.

    ``C = S*N(d1) - K*exp(-rT)*N(d2)``. Used to price the ATM straddle from each
    leg's stored IV (``greeks/straddle.py``) -- an ADR-002 BS-synthesis use, since
    the normalized chain carries ``iv`` but no option premium.
    """
    sqrt_t = np.sqrt(t)
    d1 = (np.log(spot / strike) + (r + 0.5 * sigma**2) * t) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    return spot * norm_cdf(d1) - strike * np.exp(-r * t) * norm_cdf(d2)


def bs_put_price(
    spot: float | np.ndarray,
    strike: np.ndarray,
    sigma: np.ndarray,
    t: np.ndarray,
    r: float = 0.0,
) -> np.ndarray:
    """Black-Scholes put price, no dividends; broadcasts against ``spot``.

    ``P = K*exp(-rT)*N(-d2) - S*N(-d1)``. Satisfies put-call parity with
    ``bs_call_price``: ``C - P = S - K*exp(-rT)``.
    """
    sqrt_t = np.sqrt(t)
    d1 = (np.log(spot / strike) + (r + 0.5 * sigma**2) * t) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    return strike * np.exp(-r * t) * norm_cdf(-d2) - spot * norm_cdf(-d1)


def dollar_gamma(
    spot: float | np.ndarray,
    strike: np.ndarray,
    sigma: np.ndarray,
    t: np.ndarray,
    oi: np.ndarray,
    sign: np.ndarray,
    *,
    multiplier: float | np.ndarray = 100.0,
    r: float = 0.0,
) -> np.ndarray:
    """Sign-weighted dealer dollar-gamma per 1% move at ``spot``.

    ``sign * gamma_BS * oi * multiplier * spot^2 * 0.01`` (calls +1, puts -1).
    """
    gamma = bs_gamma(spot, strike, sigma, t, r)
    return sign * gamma * oi * multiplier * np.asarray(spot) ** 2 * 0.01


def years_to_expiry(expiration: pd.Series, ref: date) -> np.ndarray:
    """Convert an ``expiration`` column to years-to-expiry (floored at ~1 hour).

    Handles datetimes/date strings (calendar diff), Convex epoch-day integers,
    and plain days-to-expiry.
    """
    ref_ts = pd.Timestamp(ref)
    if pd.api.types.is_datetime64_any_dtype(expiration):
        parsed = pd.to_datetime(expiration, errors="coerce")
        years = (parsed - ref_ts).dt.total_seconds().to_numpy() / (365.0 * 24 * 3600)
        return np.maximum(years, _MIN_T)
    numeric = pd.to_numeric(expiration, errors="coerce")
    if numeric.notna().all():
        if float(numeric.median()) >= _EPOCH_DAY_THRESHOLD:
            dates = pd.to_datetime(numeric, unit="D", origin="unix")
            years = (dates - ref_ts).dt.total_seconds().to_numpy() / (365.0 * 24 * 3600)
        else:
            years = numeric.to_numpy(dtype=float) / 365.0
    else:
        parsed = pd.to_datetime(expiration, errors="coerce")
        years = (parsed - ref_ts).dt.total_seconds().to_numpy() / (365.0 * 24 * 3600)
    return np.maximum(years, _MIN_T)
