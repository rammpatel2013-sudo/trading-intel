"""Aggregate Greek-exposure computation.

Given a normalized options-chain DataFrame (as produced by an
``OptionsDataSource.chain()`` implementation), compute the regime descriptors
GEX / DEX / VEX / CHEX plus a spot-anchored ATM IV.

FlashAlpha rule (CLAUDE.md rule 4): these are *regime descriptors*, not signals.
Nothing here emits an alert. Only validated ``strategies/`` modules write signals.

Units (decided 2026-05-21, validated against the ConvexValue app): GEX/DEX are
the **raw net signed** Convex fields so they line up 1:1 with what Mithil sees
in the Convex `gxoi`/`dxoi` panels (calls +, puts −). Convex's `gxoi`/`dxoi`
are already `greek × oi` per-share; we do NOT apply the contract multiplier or
spot² dollar-scaling (that gave SpotGamma-style $ GEX, which we dropped in
favour of matching the source).

    GEX  = Σ  sign · gxoi                 (sign = +1 calls, −1 puts)  → net gxoi
    DEX  = Σ  dxoi                         (dxoi already carries call/put sign)
    VEX  = Σ  vanna · oi · spot · iv
    CHEX = Σ  charm · oi · spot · 365
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from trading_intel.errors import ComputationError

# Sign convention: long calls add positive gamma, long puts subtract.
_SIGN = {"C": 1.0, "P": -1.0}

# Columns required for a full exposures computation.
_REQUIRED = ("opt_kind", "strike", "gxoi", "dxoi", "vanna", "charm", "oi", "iv")


def _sign_series(chain: pd.DataFrame) -> pd.Series:
    """Map the call/put column to +1 / −1, raising on unknown kinds."""
    sign = chain["opt_kind"].astype(str).str.upper().str[0].map(_SIGN)
    if sign.isna().any():
        bad = sorted(chain.loc[sign.isna(), "opt_kind"].astype(str).unique())
        raise ComputationError(f"Unrecognized opt_kind values in chain: {bad}")
    return sign


def atm_iv(chain: pd.DataFrame, spot: float, *, n_strikes: int = 4) -> float | None:
    """Mean IV of the ``n_strikes`` strikes closest to spot. ``None`` if no IV."""
    if chain.empty or "iv" not in chain.columns or "strike" not in chain.columns:
        return None
    iv = pd.to_numeric(chain["iv"], errors="coerce")
    strike = pd.to_numeric(chain["strike"], errors="coerce")
    mask = iv.notna() & strike.notna()
    if not mask.any():
        return None
    distance = (strike[mask] - spot).abs()
    nearest_idx = distance.nsmallest(min(n_strikes, int(mask.sum()))).index
    value = float(iv.loc[nearest_idx].mean())
    return value if np.isfinite(value) else None


def compute_exposures(chain: pd.DataFrame, spot: float) -> dict:
    """Aggregate GEX/DEX/VEX/CHEX + ATM IV for a single underlying.

    Args:
        chain: normalized options chain. Required columns:
            ``opt_kind, strike, gxoi, dxoi, vanna, charm, oi, iv``.
        spot: current underlying price (anchors VEX/CHEX scaling + ATM IV).

    Returns a dict with: ``gex_total, dex_total, vex_total, chex_total,
    atm_iv``. Empty dict for an empty chain.

    ``gex_total`` is net signed gxoi — matches the ConvexValue app's headline
    figure (e.g. AAPL ≈ 3.70k near-term).
    """
    if chain is None or chain.empty:
        return {}

    missing = [c for c in _REQUIRED if c not in chain.columns]
    if missing:
        raise ComputationError(f"Chain is missing required columns: {missing}")
    if not np.isfinite(spot) or spot <= 0:
        raise ComputationError(f"Invalid spot for exposures: {spot!r}")

    df = chain.copy()
    for col in ("gxoi", "dxoi", "vanna", "charm", "oi", "iv"):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    sign = _sign_series(df)

    # Net signed gxoi — matches the ConvexValue display (calls +, puts −).
    gex_total = float((df["gxoi"] * sign).sum())
    # dxoi already carries the natural call/put sign, so just sum it.
    dex_total = float(df["dxoi"].sum())
    # Vanna/charm exposures recomputed from raw greeks (Convex's vxoi is
    # vega-based, not vanna-based — see locked-formula note above).
    vex_total = float((df["vanna"] * df["oi"] * spot * df["iv"]).sum())
    chex_total = float((df["charm"] * df["oi"] * spot * 365.0).sum())

    return {
        "gex_total": gex_total,
        "dex_total": dex_total,
        "vex_total": vex_total,
        "chex_total": chex_total,
        "atm_iv": atm_iv(df, spot),
    }
