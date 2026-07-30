"""Delta flip point (zero-DEX price) via Black-Scholes repricing.

The delta flip is the underlying price at which net open-interest delta
(Σ δ·OI, calls +, puts − — the same quantity ``exposures.dex_total`` reports)
crosses zero. Below it the option book carries net-negative delta, above it
net-positive. It is the directional-lean analog of the gamma flip
(``flip_point.py``) and, like it, a *regime descriptor*, not a signal
(CLAUDE.md rule 4).

Net delta is near-monotone in spot (as spot rises every call δ → 1 and every
put δ → 0), so the zero is *softer* than the zero-gamma crossing and frequently
sits outside a ±``search_range`` band — in which case this returns ``None`` (no
flip in range), by design. That asymmetry versus the sharp, ATM-peaked gamma
flip is expected, not a defect: gamma peaks ATM and crosses hard; delta drifts.

Method mirrors ``flip_point.gex_flip`` exactly, swapping gamma for delta: for
each option recompute its Black-Scholes delta at a candidate spot ``S`` from the
option's own (sticky) strike, IV and time-to-expiry, sum the signed net-OI
delta across the chain, and find the zero over ±``search_range`` of spot with
``scipy.optimize.brentq``.

    net_dex(S) = Σ  δ_BS(S; K, σ, T, cp) · oi · multiplier   (call δ>0, put δ<0)

Sign convention matches ``exposures.dex_total`` — raw net-OI delta, with NO
dealer long/short overlay (unlike the gamma flip's calls-+/puts- gamma sign).
This is deliberate: it keeps the flip consistent with the DEX figure shown
next to it. A dealer-signed variant is a one-line change (negate ``oi``) but is
intentionally not the default. Returns ``None`` if net delta does not change
sign across the search interval, or on insufficient data.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
from scipy.optimize import brentq
from scipy.special import ndtr

# Natural call/put delta sign: call δ ∈ (0, 1), put δ ∈ (−1, 0).
_SIGN = {"C": 1.0, "P": -1.0}
_DEFAULT_MULTIPLIER = 100.0
_MIN_T = 1.0 / (365.0 * 24.0)  # floor time-to-expiry at ~1 hour to avoid /0
# Convex returns expiration as days since the Unix epoch (e.g. 20595 = 2026-05-22).
# Anything this large is clearly an epoch-day count, not a days-to-expiry value.
_EPOCH_DAY_THRESHOLD = 10_000


def _years_to_expiry(expiration: pd.Series, ref: date) -> np.ndarray:
    """Convert an ``expiration`` column to years-to-expiry (floored at ~1 hour).

    Handles datetimes/date strings (calendar diff), Convex epoch-day integers,
    and plain days-to-expiry — identical handling to ``flip_point`` so the two
    flips agree on the time axis.
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


def _bs_call_delta(
    spot: float, strike: np.ndarray, sigma: np.ndarray, t: np.ndarray, r: float
) -> np.ndarray:
    """Black-Scholes CALL delta N(d1) for an array of options at a single spot.

    Put delta is ``N(d1) − 1`` (no-dividend); the caller applies that offset so
    this mirrors ``flip_point._bs_gamma`` (unsigned magnitude, sign at the call
    site).
    """
    sqrt_t = np.sqrt(t)
    d1 = (np.log(spot / strike) + (r + 0.5 * sigma**2) * t) / (sigma * sqrt_t)
    return ndtr(d1)


def dex_flip(
    chain: pd.DataFrame,
    spot: float,
    *,
    risk_free_rate: float = 0.04,
    search_range: float = 0.10,
) -> float | None:
    """Return the zero net-OI-delta price within ±``search_range`` of ``spot``.

    Needs ``opt_kind, strike, iv, oi, expiration`` columns (``multiplier``
    optional, default 100) — the SAME contract as :func:`flip_point.gex_flip`, so
    it drops into any caller that already computes the gamma flip. Returns
    ``None`` if net delta does not change sign in range or data is insufficient.
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
    is_put = (sign[valid].to_numpy(dtype=float) < 0.0)
    strike_a = strike[valid].to_numpy(dtype=float)
    sigma_a = sigma[valid].to_numpy(dtype=float)
    oi_a = oi[valid].to_numpy(dtype=float)
    mult_a = mult[valid].to_numpy(dtype=float)
    years_a = years[vmask]

    def net_dex(price: float) -> float:
        call_delta = _bs_call_delta(price, strike_a, sigma_a, years_a, risk_free_rate)
        # put δ = N(d1) − 1; call δ = N(d1). Naturally signed (call +, put −).
        signed_delta = np.where(is_put, call_delta - 1.0, call_delta)
        return float(np.sum(signed_delta * oi_a * mult_a))

    lo, hi = spot * (1.0 - search_range), spot * (1.0 + search_range)
    d_lo, d_hi = net_dex(lo), net_dex(hi)
    if not (np.isfinite(d_lo) and np.isfinite(d_hi)) or d_lo * d_hi > 0:
        return None  # no sign change in the bracket → no delta flip in range

    return float(brentq(net_dex, lo, hi, xtol=1e-2, maxiter=100))


def classify_delta(
    spot: float, dex_flip_price: float | None, dex_total: float | None
) -> dict:
    """Label the delta regime from spot, the flip level, and net DEX.

    Descriptor only (rule 4) — the delta-side companion to the gamma regime
    label. ``lean`` reads the standing net-OI-delta sign; ``side`` and
    ``dist_to_flip`` locate spot relative to the zero-DEX price when it exists.

    Returns a dict ready to drop onto the cockpit's Net-DEX cell::

        {"lean": "net long delta"|"net short delta"|"flat",
         "delta_flip": float|None,
         "side": "above delta flip"|"below delta flip"|None,
         "dist_to_flip": float|None}   # (spot − flip)/spot, signed
    """
    dx = float(dex_total) if dex_total is not None and np.isfinite(dex_total) else 0.0
    lean = "net long delta" if dx > 0 else "net short delta" if dx < 0 else "flat"

    side: str | None = None
    dist: float | None = None
    if (
        dex_flip_price is not None
        and np.isfinite(dex_flip_price)
        and spot
        and np.isfinite(spot)
        and spot > 0
    ):
        dist = (spot - dex_flip_price) / spot
        side = "above delta flip" if spot >= dex_flip_price else "below delta flip"

    return {"lean": lean, "delta_flip": dex_flip_price, "side": side, "dist_to_flip": dist}
