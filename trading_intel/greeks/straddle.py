"""ATM straddle price + expected-move range from the options chain.

The at-the-money straddle price (ATM call + ATM put) is the market's compact
expected-move estimate: ``spot +/- straddle`` brackets the day's likely range.
The ATM straddle runs about 0.8 sigma, so it is a touch tighter than a full
+/-1 sigma IV*sqrt(t) cone (``prices/price_cone.py``) -- the two are
complementary, not duplicates.

VS3D uses ``spot +/- straddle`` as its headline range and "is the straddle
*decaying*?" as the charm-validity cross-check: charm only leads intraday when
the straddle is bleeding; a straddle repricing *up* means vol is richening and
other flows are overpowering charm (Dan's "snake-oil tell"). See
``docs/learning/vs3d-dealer-exposure-digest.md``.

The normalized chain carries per-strike ``iv`` but no option premium, so the
straddle is priced with Black-Scholes from each ATM leg's own IV. This is an
ADR-002 BS-synthesis use (like the flip-point repricing), NOT a new vendor field
(rule 1). At the money the model and market straddle track closely; the decay
check only needs the *change*, which decomposes cleanly into IV repricing
(straddle up) vs time decay (straddle down).

Regime descriptor only -- emits no signals (FlashAlpha rule 4).
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from trading_intel.errors import ComputationError
from trading_intel.greeks.black_scholes import bs_call_price, bs_put_price, years_to_expiry

_REQUIRED = ("opt_kind", "strike", "iv", "expiration")


def _leg_iv(rows: pd.DataFrame, code: str) -> float | None:
    """Mean IV of the ``code`` side (``C``/``P``) at the ATM strike, else None."""
    leg = rows[rows["_side"] == code]
    if leg.empty:
        return None
    val = float(pd.to_numeric(leg["iv"], errors="coerce").mean())
    return val if np.isfinite(val) and val > 0 else None


def atm_straddle(
    chain: pd.DataFrame,
    spot: float,
    *,
    ref_date: date | None = None,
    risk_free_rate: float = 0.0,
) -> dict:
    """ATM straddle price + expected-move bounds for the front expiration.

    Picks the nearest (smallest positive time-to-expiry) expiration present in
    ``chain``, finds the strike closest to ``spot``, and prices a Black-Scholes
    straddle (ATM call + ATM put) from each leg's stored ``iv``. Pass a chain
    pre-filtered to one expiration to force a horizon (e.g. a ~30-DTE slice for a
    swing view rather than the front-week straddle).

    Args:
        chain: normalized options chain. Required columns:
            ``opt_kind`` (C/P), ``strike``, ``iv``, ``expiration``. ``expiration``
            may be datetimes, Convex epoch-day ints, or plain days-to-expiry
            (handled by ``black_scholes.years_to_expiry``).
        spot: current underlying price (anchors the ATM strike + pricing).
        ref_date: valuation date for time-to-expiry (defaults to today).
        risk_free_rate: annualized ``r`` for discounting (default 0).

    Returns a dict: ``straddle, atm_strike, dte, t_years, atm_iv, call_price,
    put_price, upper, lower, straddle_pct, spot``. Empty dict for an empty chain.
    """
    if chain is None or chain.empty:
        return {}
    missing = [c for c in _REQUIRED if c not in chain.columns]
    if missing:
        raise ComputationError(f"Straddle chain missing columns: {missing}")
    if not np.isfinite(spot) or spot <= 0:
        raise ComputationError(f"Invalid spot for straddle: {spot!r}")

    df = chain.copy()
    df["strike"] = pd.to_numeric(df["strike"], errors="coerce")
    df["iv"] = pd.to_numeric(df["iv"], errors="coerce")
    df["_side"] = df["opt_kind"].astype(str).str.upper().str[0]
    df["_t"] = years_to_expiry(df["expiration"], ref_date or date.today())

    df = df[df["strike"].notna() & df["iv"].notna() & (df["iv"] > 0) & (df["_t"] > 0)]
    if df.empty:
        raise ComputationError("No priceable straddle rows (need strike, iv>0, t>0)")

    # Front expiration = smallest positive time-to-expiry.
    t_front = float(df["_t"].min())
    front = df[np.isclose(df["_t"], t_front)]

    # ATM strike = closest listed strike to spot within the front expiration.
    atm_strike = float(front.loc[(front["strike"] - spot).abs().idxmin(), "strike"])
    at = front[np.isclose(front["strike"], atm_strike)]

    iv_c = _leg_iv(at, "C")
    iv_p = _leg_iv(at, "P")
    if iv_c is None and iv_p is None:
        raise ComputationError(f"No usable ATM call/put IV at strike {atm_strike}")
    # If one leg is missing, price both off the available IV (ATM put vol ~ call).
    iv_c = iv_c if iv_c is not None else iv_p
    iv_p = iv_p if iv_p is not None else iv_c

    call_px = float(bs_call_price(spot, atm_strike, iv_c, t_front, risk_free_rate))
    put_px = float(bs_put_price(spot, atm_strike, iv_p, t_front, risk_free_rate))
    straddle = call_px + put_px

    return {
        "straddle": straddle,
        "atm_strike": atm_strike,
        "dte": round(t_front * 365.0, 1),
        "t_years": t_front,
        "atm_iv": float(np.mean([iv_c, iv_p])),
        "call_price": call_px,
        "put_price": put_px,
        "upper": float(spot) + straddle,
        "lower": float(spot) - straddle,
        "straddle_pct": straddle / float(spot) * 100.0,
        "spot": float(spot),
    }


def straddle_decay(current: float, reference: float, *, flat_pct: float = 1.0) -> dict:
    """Classify the ATM-straddle change vs a prior reading (charm cross-check).

    Charm is only the dominant intraday force when the straddle is *decaying*. A
    straddle repricing *up* means vol is richening and other flows are
    overpowering charm -- treat charm reads as unreliable there. ``flat_pct`` is
    the dead-band (percent of ``reference``) inside which the move is called flat.

    Returns ``{current, reference, change, pct_change, label, charm_supported}``
    where ``label`` is one of {``decaying``, ``repricing_up``, ``flat``} and
    ``charm_supported`` is True only when decaying.
    """
    for name, val in (("current", current), ("reference", reference)):
        if val is None or not np.isfinite(val):
            raise ComputationError(f"straddle_decay: {name} must be finite, got {val!r}")
    if reference <= 0:
        raise ComputationError(f"straddle_decay: reference must be > 0, got {reference!r}")

    change = float(current) - float(reference)
    pct = change / float(reference) * 100.0
    if abs(pct) <= flat_pct:
        label = "flat"
    elif change < 0:
        label = "decaying"
    else:
        label = "repricing_up"
    return {
        "current": float(current),
        "reference": float(reference),
        "change": change,
        "pct_change": pct,
        "label": label,
        "charm_supported": label == "decaying",
    }
