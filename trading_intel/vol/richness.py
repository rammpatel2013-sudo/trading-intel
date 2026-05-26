"""Volatility-richness ranking — IV rich/cheap vs a forward RV forecast.

The core edge of the vol-richness scanner (MEMORY "Vol-richness scanner"):

    vrp_pts = IV_atm(h) - forecastRV(h)

A positive VRP means the option market is pricing more vol than we forecast will
be realized (premium-rich); negative means cheap. The raw points are not
comparable across names, so each is standardized to the symbol's OWN history:

- **VRP percentile** — where today's VRP sits in its trailing distribution
  (0..1). This is the richness score.
- **IV rank** — the classic ``(iv - min) / (max - min)`` over the trailing IV
  window (0..1): is implied vol high or low for this name regardless of RV.

ATM IV is taken from ``greeks.surface.DeltaSurface.atm_iv`` (per listed expiry)
and interpolated to the target horizon in **total-variance space** (the
constant-maturity convention: linear in ``iv^2 * t``), so a 30d / 60d read is
consistent even when no expiry lands exactly there.

Pure functions only (arrays/sequences in, numbers/frame out): the EOD
``vol_richness`` job supplies the live surface + the trailing history it reads
from the un-pruned ``vol_richness`` table. Standardization is **cold** until a
name has enough history — those rows return ``None`` stats and a ``cold`` label
rather than a misleading score.

Regime descriptor only (FlashAlpha rule 4) — a rich/cheap read, never a signal.
The short-vol tail-risk gate lives in ``vol.term_skew`` and is applied on top.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

#: Below this many trailing observations, percentile / IV-rank are not meaningful.
MIN_HISTORY = 20

#: VRP-percentile thresholds for the descriptive richness label.
RICH_PCTILE = 0.80
CHEAP_PCTILE = 0.20


# ── ATM-IV term-structure interpolation ────────────────────────────────


def atm_iv_at_horizon(
    dte: Sequence[float] | np.ndarray,
    atm_iv: Sequence[float] | np.ndarray,
    horizon_dte: float,
) -> float | None:
    """ATM IV at ``horizon_dte`` via constant-maturity (total-variance) interp.

    Interpolates linearly in total variance ``w = iv^2 * t`` between the two
    bracketing expiries, then returns ``sqrt(w / h)``. Horizons inside the
    observed tenor span are interpolated; outside it, clamped to the nearest
    expiry's IV (no variance extrapolation). Returns ``None`` if there are no
    usable expiries.
    """
    pairs = sorted(
        (float(d), float(v))
        for d, v in zip(dte, atm_iv, strict=False)
        if np.isfinite(d) and np.isfinite(v) and d > 0 and v > 0
    )
    if not pairs:
        return None
    ds = [p[0] for p in pairs]
    vs = [p[1] for p in pairs]
    h = float(horizon_dte)
    if h <= ds[0]:
        return vs[0]
    if h >= ds[-1]:
        return vs[-1]
    for i in range(1, len(ds)):
        if ds[i] >= h:
            d0, d1, v0, v1 = ds[i - 1], ds[i], vs[i - 1], vs[i]
            w0, w1 = v0**2 * d0, v1**2 * d1
            w = w0 + (w1 - w0) * (h - d0) / (d1 - d0)
            return float(np.sqrt(w / h)) if w > 0 else None
    return vs[-1]


# ── Standardization helpers ────────────────────────────────────────────


def compute_vrp(iv_atm: float, forecast_rv: float) -> float:
    """Variance-risk-premium proxy in vol points: ``iv_atm - forecast_rv``."""
    return float(iv_atm) - float(forecast_rv)


def percentile_rank(
    history: Sequence[float], current: float, *, min_history: int = MIN_HISTORY
) -> float | None:
    """Fraction of ``history`` <= ``current`` (0..1). ``None`` if history is cold."""
    vals = [float(x) for x in history if np.isfinite(x)]
    if len(vals) < min_history:
        return None
    arr = np.asarray(vals)
    return float(np.mean(arr <= float(current)))


def iv_rank(
    history: Sequence[float], current: float, *, min_history: int = MIN_HISTORY
) -> float | None:
    """Classic IV rank ``(current - min) / (max - min)`` over history (0..1).

    ``None`` if history is cold or the trailing range is degenerate (max == min).
    """
    vals = [float(x) for x in history if np.isfinite(x)]
    if len(vals) < min_history:
        return None
    lo, hi = min(vals), max(vals)
    if hi <= lo:
        return None
    return float((float(current) - lo) / (hi - lo))


def classify_richness(vrp_pctile: float | None) -> str:
    """Descriptive label from the VRP percentile (rule 4 — not a trade call)."""
    if vrp_pctile is None:
        return "cold (insufficient history)"
    if vrp_pctile >= RICH_PCTILE:
        return "rich (premium-sell candidate, delta-hedge)"
    if vrp_pctile <= CHEAP_PCTILE:
        return "cheap (long-vol candidate)"
    return "neutral"


# ── Per-name richness row + ranking frame ──────────────────────────────


@dataclass(frozen=True)
class RichnessInputs:
    """Everything needed to score one (symbol, horizon), all from stored data."""

    symbol: str
    horizon_dte: int
    iv_atm: float
    forecast_rv: float
    iv_history: Sequence[float]  # trailing iv_atm values for this name/horizon
    vrp_history: Sequence[float]  # trailing vrp_pts values for this name/horizon


@dataclass(frozen=True)
class RichnessRow:
    """A scored richness row (descriptive)."""

    symbol: str
    horizon_dte: int
    iv_atm: float
    forecast_rv: float
    vrp_pts: float
    vrp_pctile: float | None
    iv_rank: float | None
    richness_score: float | None
    label: str


def build_richness_row(
    inputs: RichnessInputs, *, min_history: int = MIN_HISTORY
) -> RichnessRow:
    """Score one (symbol, horizon): VRP, percentile, IV rank, label."""
    vrp = compute_vrp(inputs.iv_atm, inputs.forecast_rv)
    pctile = percentile_rank(inputs.vrp_history, vrp, min_history=min_history)
    rank = iv_rank(inputs.iv_history, inputs.iv_atm, min_history=min_history)
    return RichnessRow(
        symbol=inputs.symbol.upper(),
        horizon_dte=inputs.horizon_dte,
        iv_atm=float(inputs.iv_atm),
        forecast_rv=float(inputs.forecast_rv),
        vrp_pts=vrp,
        vrp_pctile=pctile,
        iv_rank=rank,
        richness_score=pctile,  # the percentile IS the standardized richness
        label=classify_richness(pctile),
    )


_FRAME_COLUMNS = [
    "symbol",
    "horizon_dte",
    "iv_atm",
    "forecast_rv",
    "vrp_pts",
    "vrp_pctile",
    "iv_rank",
    "richness_score",
    "label",
]


def rank_richness(rows: Sequence[RichnessRow]) -> pd.DataFrame:
    """Tidy ranking frame, richest first (cold rows sort last).

    Sorted by ``richness_score`` descending within each horizon; rows with no
    score (cold start) fall to the bottom. Returns an empty, correctly-typed
    frame when given no rows.
    """
    if not rows:
        return pd.DataFrame(columns=_FRAME_COLUMNS)
    frame = pd.DataFrame([r.__dict__ for r in rows], columns=_FRAME_COLUMNS)
    return frame.sort_values(
        by=["horizon_dte", "richness_score", "vrp_pts"],
        ascending=[True, False, False],
        na_position="last",
    ).reset_index(drop=True)
