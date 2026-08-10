"""Spot-ladder dealer GAMMA / CHARM / VANNA profiles (extends gamma_profile.py, ADR-002/004).

Generalizes the VS3D "$greek vs spot reference" curve to all three second-order
greeks the newsletters plot (gamma ceiling, charm-into-close, vanna-on-vol):
for a grid of hypothetical spot levels, recompute each option's Black-Scholes
greek and sum the sign-weighted dealer dollar-exposure, grouped by expiration
(so 0DTE / today's expiry can be shaded against the full book).

Reuses the *validated* closed forms in ``greeks/black_scholes.py`` (bs_gamma /
bs_charm / bs_vanna — FD-checked) so the math lives in one place. Sticky-strike:
each strike keeps its own stored IV as spot moves (the VS3D / project convention).

Sign convention matches ``exposures.py`` / ``gamma_profile.py``: dealer dollar
exposure, calls +1, puts -1. Dollar scalings (per the locked exposures units):

    gamma$ = sign · gamma · oi · mult · S^2 · 0.01     ($ delta per 1% move)
    charm$ = sign · (charm/365) · oi · mult · S         ($ delta decay per day)
    vanna$ = sign · vanna · oi · mult · S · 0.01         ($ delta per 1 vol-point)

The aggregate curve's zero-crossing near spot is that greek's flip level
(gamma-flip is the familiar one; charm/vanna flips are the same idea).

Descriptor data only (CLAUDE.md rule 4). Reader wires this to ``oi_chain_eod``
via the same normalized-chain shape ``dashboard/gamma_profile_data.py`` already
builds; expose through an MCP ``get_profile`` tool.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from trading_intel.greeks.black_scholes import (
    bs_charm,
    bs_gamma,
    bs_vanna,
    years_to_expiry,
)

_NEEDED = {"opt_kind", "strike", "iv", "oi", "expiration"}
_SIGN = {"C": 1.0, "P": -1.0}
SPOT_COL = "spot_ref"

# greek -> (closed form, dollar-scaling of the per-strike contribution at spot S)
_GREEKS = {
    "gamma": (bs_gamma, lambda g, oi, mult, S: g * oi * mult * S**2 * 0.01),
    "charm": (bs_charm, lambda g, oi, mult, S: (g / 365.0) * oi * mult * S),
    "vanna": (bs_vanna, lambda g, oi, mult, S: g * oi * mult * S * 0.01),
}


def _sign(chain: pd.DataFrame) -> np.ndarray:
    return chain["opt_kind"].astype(str).str.upper().str[0].map(_SIGN).to_numpy(dtype=float)


def greek_profiles(
    chain: pd.DataFrame,
    spot: float,
    *,
    span: float = 0.05,
    n_points: int = 141,
    multiplier: float = 100.0,
    ref: date | None = None,
    greeks: tuple[str, ...] = ("gamma", "charm", "vanna"),
) -> dict:
    """Per-spot dealer $greek profiles (sticky-strike), one entry per greek.

    Args:
        chain: normalized chain — needs ``opt_kind, strike, iv, oi, expiration``.
        spot: current underlying.
        span/n_points: ± fraction of spot and grid resolution.
        greeks: any of ``gamma``/``charm``/``vanna``.

    Returns ``{"spot_ref": [...], <greek>: {"all": [...], "by_expiry": {label: [...]},
    "flip": float|None}}``. Empty dict for an unusable chain.
    """
    if chain is None or chain.empty or not _NEEDED.issubset(chain.columns):
        return {}
    if not np.isfinite(spot) or spot <= 0:
        return {}

    df = chain.dropna(subset=["strike", "iv", "oi", "expiration"]).copy()
    df = df[(df["iv"] > 0) & (df["oi"] > 0)]
    if df.empty:
        return {}

    grid = np.linspace(spot * (1 - span), spot * (1 + span), n_points)
    sign = _sign(df)
    K = df["strike"].to_numpy(dtype=float)
    sig = df["iv"].to_numpy(dtype=float)
    oi = df["oi"].to_numpy(dtype=float)
    t = years_to_expiry(df["expiration"], ref or date.today())
    labels = pd.to_datetime(df["expiration"], errors="coerce").dt.date.astype("string").to_numpy()

    out: dict = {SPOT_COL: grid.round(4).tolist()}
    for name in greeks:
        form, scale = _GREEKS[name]
        # per (grid_point, strike) matrix of the raw greek, then dollar-scaled + signed
        raw = np.vstack([form(S, K, sig, t) for S in grid])          # (n_grid, n_strike)
        contrib = scale(raw, oi, multiplier, grid[:, None]) * sign[None, :]
        total = contrib.sum(axis=1) / 1e9                            # $bn units
        # per-expiry split (for 0DTE / today shading)
        by_exp: dict[str, list] = {}
        for lab in pd.unique(labels):
            mask = labels == lab
            by_exp[str(lab)] = (contrib[:, mask].sum(axis=1) / 1e9).round(6).tolist()
        # flip = zero-crossing of the aggregate nearest spot
        flip = None
        s = np.sign(total)
        for i in range(1, len(grid)):
            if s[i - 1] < 0 <= s[i]:
                flip = float(np.interp(0.0, [total[i - 1], total[i]], [grid[i - 1], grid[i]]))
        out[name] = {"all": total.round(6).tolist(), "by_expiry": by_exp, "flip": flip}
    return out
