"""Pure return-series statistics for the backtest harness.

All functions are NumPy-only, DB-free, and degrade to ``None`` on insufficient
input rather than raising — mirroring the cold-row contract used throughout
``vol/`` and ``strategies/``.

No annualization is applied to Sharpe-like ratios here. The harness reports
``mean / std`` of forward-H-day returns directly (an information ratio per
H-day bet). Annualizing requires non-overlapping samples; the regime backtest
inherently overlaps (signals fire daily, H>1), so annualized Sharpe would be
misleading. Consumers that need an annualized figure can do their own
``mean/std * sqrt(252/H)`` with eyes open.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class ReturnStats:
    """Distribution summary for a return series.

    ``hit_rate`` counts strictly positive returns. ``ir`` is ``mean / std``
    (information ratio of one H-day bet, NOT annualized — see module docstring).
    All values are ``None`` when the underlying series has fewer than
    :data:`MIN_SAMPLES` observations or degenerates.
    """

    n: int
    mean: float | None
    median: float | None
    std: float | None
    ir: float | None
    hit_rate: float | None
    p05: float | None
    p25: float | None
    p75: float | None
    p95: float | None
    min: float | None
    max: float | None


#: Minimum observations before any statistic is computed. Below this, every
#: field except ``n`` is ``None``.
MIN_SAMPLES = 5


def _empty(n: int) -> ReturnStats:
    return ReturnStats(
        n=n,
        mean=None,
        median=None,
        std=None,
        ir=None,
        hit_rate=None,
        p05=None,
        p25=None,
        p75=None,
        p95=None,
        min=None,
        max=None,
    )


def summarize(returns: np.ndarray) -> ReturnStats:
    """Compute the full distribution summary for a 1-D return array.

    Non-finite values are dropped before scoring. Returns ``None``-filled
    :class:`ReturnStats` if fewer than :data:`MIN_SAMPLES` survive.
    """
    if returns is None:
        return _empty(0)
    arr = np.asarray(returns, dtype=float).ravel()
    arr = arr[np.isfinite(arr)]
    n = int(arr.size)
    if n < MIN_SAMPLES:
        return _empty(n)

    mean = float(arr.mean())
    std = float(arr.std(ddof=1)) if n > 1 else 0.0
    median = float(np.median(arr))
    hit_rate = float((arr > 0).mean())
    p05, p25, p75, p95 = (float(x) for x in np.quantile(arr, [0.05, 0.25, 0.75, 0.95]))
    lo = float(arr.min())
    hi = float(arr.max())
    ir = mean / std if std > 0 else None

    return ReturnStats(
        n=n,
        mean=mean,
        median=median,
        std=std,
        ir=ir,
        hit_rate=hit_rate,
        p05=p05,
        p25=p25,
        p75=p75,
        p95=p95,
        min=lo,
        max=hi,
    )


def lift_vs_baseline(state_stats: ReturnStats, baseline: ReturnStats) -> float | None:
    """How much the conditional mean exceeds the unconditional mean.

    Positive = the regime is a forward-tailwind. Negative = a forward-headwind.
    Returns ``None`` if either side lacks a mean.
    """
    if state_stats.mean is None or baseline.mean is None:
        return None
    return state_stats.mean - baseline.mean
