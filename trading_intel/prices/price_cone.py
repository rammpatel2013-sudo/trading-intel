"""Forward price cone (HAR-RV driven): expected-range bands over a horizon.

Projects spot forward over the next ~month as lognormal +/-1sigma / +/-2sigma
bands that widen with sqrt(time), using an annualized vol (typically the HAR-RV
forecast from ``prices.forecast_vol``). Drift is assumed zero -- this is a vol
cone (an expected-range envelope), not a directional call. Descriptive regime
view, not a signal (FlashAlpha rule 4).

At trading day ``t`` ahead, the cumulative sigma is ``ann_vol * sqrt(t / 252)``
and the bands are ``spot * exp(+/- z * sigma_t)`` for ``z`` in {1, 2}.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

_TRADING_DAYS = 252
_COLS = ["day", "median", "lo1", "hi1", "lo2", "hi2"]


def forward_cone(
    spot: float | None,
    ann_vol: float | None,
    *,
    horizon_days: int = 21,
    trading_days: int = _TRADING_DAYS,
) -> pd.DataFrame:
    """Lognormal forward cone over ``horizon_days`` trading days (zero drift).

    Columns: ``day`` (1..horizon), ``median`` (= spot), and ``lo1/hi1`` (+/-1sigma)
    and ``lo2/hi2`` (+/-2sigma). Empty frame on invalid/missing inputs.
    """
    if (
        spot is None or ann_vol is None
        or not np.isfinite(spot) or not np.isfinite(ann_vol)
        or spot <= 0 or ann_vol <= 0 or horizon_days < 1
    ):
        return pd.DataFrame(columns=_COLS)
    days = np.arange(1, int(horizon_days) + 1, dtype=float)
    sigma_t = ann_vol * np.sqrt(days / trading_days)
    out = pd.DataFrame({"day": days.astype(int), "median": float(spot)})
    out["lo1"] = spot * np.exp(-1.0 * sigma_t)
    out["hi1"] = spot * np.exp(1.0 * sigma_t)
    out["lo2"] = spot * np.exp(-2.0 * sigma_t)
    out["hi2"] = spot * np.exp(2.0 * sigma_t)
    return out[_COLS]
