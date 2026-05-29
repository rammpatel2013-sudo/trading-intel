"""Nations CallDex/PutDex/RiskDex proxies from a ``DeltaSurface``.

Nations Indexes publishes CallDex®, PutDex®, RiskDex® as normalized 30d
1-sigma-OTM call/put prices and their ratio, but does **not** distribute the
levels on Yahoo (subscription-only via SpiderRock). We compute *proxies* from
the SPX (or SPY) delta surface we already build for the skew job, so the regime
classifier in ``strategies/vol_regime.py`` has all five Nations descriptors
available.

The proxies use IV at 15Δ (the nearest grid point to the 1σ ≈ 16Δ Nations
methodology), at the 30d horizon, expressed in vol points (IV × 100). They are
not numerically identical to the published Nations values, but capture the same
information — the relative cost of out-of-the-money calls vs puts vs ATM.
Documented as ``*_proxy`` in the schema so consumers cannot mistake them.

These are regime descriptors per CLAUDE.md rule 4 (not signals). Only
``strategies/vol_regime.py`` (or the probability model in Phase 5) may emit
alerts from them.
"""

from __future__ import annotations

import numpy as np

from trading_intel.greeks.surface import DeltaSurface

#: Δ-grid point closest to the Nations 1σ-OTM (16Δ) reference. Surface's default
#: deltas include 15 and 20; 15 is the nearest legal pick.
DEX_DELTA = 15.0

#: Horizon for the Nations Dex family is 30d.
DEX_HORIZON_DTE = 30


def _expiry_index(surface: DeltaSurface, horizon_dte: int) -> int | None:
    if surface.n_expiries == 0:
        return None
    return int(np.argmin(np.abs(surface.dte - horizon_dte)))


def _delta_index(surface: DeltaSurface, delta: float) -> int:
    return int(np.argmin(np.abs(surface.deltas - float(delta))))


def calldex_proxy(
    surface: DeltaSurface,
    *,
    delta: float = DEX_DELTA,
    horizon_dte: int = DEX_HORIZON_DTE,
) -> float | None:
    """IV at ~16Δ call, 30d, in vol points. CallDex® proxy.

    Returns ``None`` when the surface has no expiries or the wing IV is NaN
    (cold-row contract — never emit a misleading value).
    """
    j = _expiry_index(surface, horizon_dte)
    if j is None:
        return None
    i = _delta_index(surface, delta)
    iv = float(surface.iv_call[j, i])
    if not np.isfinite(iv):
        return None
    return iv * 100.0


def putdex_proxy(
    surface: DeltaSurface,
    *,
    delta: float = DEX_DELTA,
    horizon_dte: int = DEX_HORIZON_DTE,
) -> float | None:
    """IV at ~16Δ put, 30d, in vol points. PutDex® proxy."""
    j = _expiry_index(surface, horizon_dte)
    if j is None:
        return None
    i = _delta_index(surface, delta)
    iv = float(surface.iv_put[j, i])
    if not np.isfinite(iv):
        return None
    return iv * 100.0


def riskdex_proxy(
    surface: DeltaSurface,
    *,
    delta: float = DEX_DELTA,
    horizon_dte: int = DEX_HORIZON_DTE,
) -> float | None:
    """``putdex_proxy / calldex_proxy``. RiskDex® proxy.

    Ratio > 1 → puts richer than calls (the usual equity smirk).
    Ratio < 1 → call bias (rare, classic euphoria / squeeze setup).
    ``None`` if either leg fails or CallDex proxy is zero.
    """
    p = putdex_proxy(surface, delta=delta, horizon_dte=horizon_dte)
    c = calldex_proxy(surface, delta=delta, horizon_dte=horizon_dte)
    if p is None or c is None or c == 0.0:
        return None
    return p / c


def compute_dex_triplet(
    surface: DeltaSurface,
    *,
    delta: float = DEX_DELTA,
    horizon_dte: int = DEX_HORIZON_DTE,
) -> tuple[float | None, float | None, float | None]:
    """``(calldex_proxy, putdex_proxy, riskdex_proxy)`` — one pass over the surface."""
    c = calldex_proxy(surface, delta=delta, horizon_dte=horizon_dte)
    p = putdex_proxy(surface, delta=delta, horizon_dte=horizon_dte)
    if p is None or c is None or c == 0.0:
        return (c, p, None)
    return (c, p, p / c)
