"""VIX-decomposition descriptors for the unified vol-regime classifier.

This module is the *math* layer for the regime classifier in
``strategies/vol_regime.py``. Each function maps to one **dimension** of the
five we decompose vol-regime into:

1. **LEVEL**        — ``vix_voli_spread`` exposes the wing-vs-ATM bias in VIX.
                      VOLI itself is the cleaner level read; the spread tells
                      you *why* VIX is where it is.
2. **SKEW**         — handled by ``vol.skew`` + ``index_skew_daily.sdex``;
                      nothing new here.
3. **TAIL**         — handled by Nations TDEX + ``vix_tail_hedging_score``;
                      nothing new here.
4. **TERM**         — ``vix_term_9d_30d`` and ``vix_term_3m_30d`` capture the
                      front-back differential. Negative ``9d_30d`` = front-
                      loaded panic (backwardation); negative ``3m_30d`` =
                      uncertainty isn't expected to persist.
5. **VOL-OF-VOL**   — ``vix_spx_beta_60d`` (realized) and
                      ``vix_options_richness`` (implied / realized) jointly
                      tell you whether VIX options are over/under-priced.

All functions are pure (numpy/pandas), DB-free, and degrade to ``None`` on
short / missing inputs — the cold-row contract that ``vol.skew.skew_percentile``
established.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# ── Rolling-window defaults ────────────────────────────────────────────

#: Trading-day window for the SPX-VIX β regression.
DEFAULT_BETA_WINDOW = 60

#: Minimum aligned pairs before a β is meaningful (cold-row contract).
DEFAULT_BETA_MIN_OBS = 40


# ── LEVEL dimension ────────────────────────────────────────────────────


def vix_voli_spread(vix: float | None, voli: float | None) -> float | None:
    """``VIX - VOLI`` — wing contribution to VIX over the ATM-only read.

    Positive = VIX is being lifted by wing prices (defensive hedging / skew
    bid). Zero or negative = ATM is the driver. The spread is in vol points.
    """
    if vix is None or voli is None:
        return None
    if not (np.isfinite(vix) and np.isfinite(voli)):
        return None
    return float(vix) - float(voli)


# ── TERM dimension ─────────────────────────────────────────────────────


def vix_term_spread(near: float | None, far: float | None) -> float | None:
    """``near - far`` — generic term spread.

    Use cases:
    - ``vix_term_spread(vix9d, vix)`` — negative = backwardation = acute stress.
    - ``vix_term_spread(vix, vix3m)`` — negative = stress expected to fade.

    Returns ``None`` if either tenor is missing.
    """
    if near is None or far is None:
        return None
    if not (np.isfinite(near) and np.isfinite(far)):
        return None
    return float(near) - float(far)


# ── VOL-OF-VOL dimension ───────────────────────────────────────────────


def vvix_vix_ratio(vvix: float | None, vix: float | None) -> float | None:
    """Annualized implied vol of VIX, normalized by VIX level.

    The raw ratio (typically 4-8) is dimensionful but the regime read is the
    ratio's z-score over its own 252d distribution.
    """
    if vvix is None or vix is None or vix <= 0:
        return None
    if not (np.isfinite(vvix) and np.isfinite(vix)):
        return None
    return float(vvix) / float(vix)


def vix_spx_beta(
    spx_closes: pd.Series,
    vix_closes: pd.Series,
    *,
    window: int = DEFAULT_BETA_WINDOW,
    min_obs: int = DEFAULT_BETA_MIN_OBS,
) -> float | None:
    """Rolling-window OLS β of ``%ΔVIX`` on ``%ΔSPX``.

    The realized sensitivity of VIX to SPX. Typically **negative** for daily
    data (VIX rises when SPX falls); magnitude in the -3 to -8 range when
    expressed against percent returns.

    Inputs must be date-indexed close series. The function aligns them on the
    inner intersection, takes percent changes, keeps the last ``window``
    aligned points, and fits ``%ΔVIX = a + β·%ΔSPX + ε``. Returns ``None`` if
    fewer than ``min_obs`` paired observations or the regression degenerates.
    """
    if spx_closes is None or vix_closes is None:
        return None
    spx = pd.to_numeric(spx_closes, errors="coerce").dropna().sort_index()
    vix = pd.to_numeric(vix_closes, errors="coerce").dropna().sort_index()
    if spx.empty or vix.empty:
        return None
    df = pd.concat([spx.rename("spx"), vix.rename("vix")], axis=1, join="inner")
    if df.empty:
        return None
    pct = df.pct_change().dropna()
    if pct.empty:
        return None
    pct = pct.tail(window)
    if len(pct) < min_obs:
        return None
    x = pct["spx"].to_numpy(dtype=float)
    y = pct["vix"].to_numpy(dtype=float)
    var_x = float(x.var(ddof=1))
    if not np.isfinite(var_x) or var_x <= 0:
        return None
    cov = float(np.cov(y, x, ddof=1)[0, 1])
    if not np.isfinite(cov):
        return None
    return cov / var_x


def vix_options_richness(
    vvix: float | None,
    vix: float | None,
    beta_vix_spx: float | None,
) -> float | None:
    """``VVIX / (|β_vix→spx| × VIX)`` — implied / β-implied realized.

    The headline VIX-options-richness metric. The denominator approximates the
    expected VVIX *if* VIX moved only at its realized β to SPX vol; the
    numerator is what VIX options are actually pricing in. Ratio:

    - ``> 1`` → VVIX above realized link → **VIX options expensive** (fade VIX
      call wings, sell VIX call credit spreads).
    - ``≈ 1`` → fair.
    - ``< 1`` → cheap (rare; precedes stress regimes where realized hasn't
      caught up to implied yet).

    Returns ``None`` whenever any input is missing or β is ~0.
    """
    if vvix is None or vix is None or beta_vix_spx is None:
        return None
    if vix <= 0 or not all(np.isfinite([vvix, vix, beta_vix_spx])):
        return None
    abs_b = abs(beta_vix_spx)
    if abs_b < 1e-6:
        return None
    return float(vvix) / (abs_b * float(vix))


# ── Convenience: compute all five dimension inputs in one call ─────────


def compute_decomposition(
    *,
    vix: float | None,
    voli: float | None,
    vvix: float | None,
    vix9d: float | None,
    vix3m: float | None,
    spx_closes: pd.Series | None = None,
    vix_closes: pd.Series | None = None,
) -> dict[str, float | None]:
    """Compute all VIX-regime descriptors in one shot.

    The ``index_skew`` job calls this once per EOD row build. Each output is
    independently nullable — a missing input degrades only its dependent
    outputs, never the rest.
    """
    beta = vix_spx_beta(spx_closes, vix_closes) if spx_closes is not None else None
    return {
        "vix_voli_spread": vix_voli_spread(vix, voli),
        "vix_term_9d_30d": vix_term_spread(vix9d, vix),
        "vix_term_3m_30d": vix_term_spread(vix3m, vix),
        "vix_spx_beta_60d": beta,
        "vvix_vix_ratio": vvix_vix_ratio(vvix, vix),
        "vix_options_richness": vix_options_richness(vvix, vix, beta),
    }
