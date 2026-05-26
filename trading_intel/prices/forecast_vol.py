"""Forward realized-volatility forecasts from a daily close series.

The vol-richness scanner needs a *forward* RV estimate to compare against ATM
implied vol (``vrp_pts = IV_atm(h) - forecastRV(h)``). Trailing rv20/rv60 (see
``realized_vol.py``) is the wrong input — it looks backward. This module supplies
the forward half:

- **HAR-RV** (Corsi 2009) — regress the next-``h``-day average daily variance on
  the daily / weekly (5d) / monthly (22d) averages of past daily variance, fit by
  OLS, then forecast one step. Captures vol's long-memory without an intraday
  feed (we proxy daily realized variance with the squared daily log return).
- **EWMA** (RiskMetrics, ``lambda=0.94``) — an exponentially-weighted variance
  whose flat-forward forecast is a robust baseline to sanity-check HAR against.

Everything is a pure transform (close series in, numbers out) so it is trivially
testable and reusable by the EOD ``vol_richness`` job and the dashboard. Outputs
are annualized vol in decimal form, directly comparable to quoted IV.

Regime descriptor only (FlashAlpha rule 4) — a vol forecast, never a signal.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from trading_intel.prices.realized_vol import log_returns

_TRADING_DAYS = 252
_CALENDAR_DAYS = 365

# HAR component lags (trading days): daily, weekly, monthly (Corsi 2009).
_LAG_DAILY = 1
_LAG_WEEKLY = 5
_LAG_MONTHLY = 22

_DEFAULT_LAMBDA = 0.94
#: Minimum aligned (features + forward target) rows required to fit HAR by OLS.
_MIN_HAR_OBS = 60
#: Default forecast horizons in CALENDAR days (30d headline, 60d ≈ VIX3M).
_DEFAULT_HORIZONS = (30, 60)


# ── Horizon helpers ────────────────────────────────────────────────────


def dte_to_trading_days(dte: int, *, trading_days: int = _TRADING_DAYS) -> int:
    """Map a calendar-day horizon to trading days (>= 1).

    e.g. 30 calendar -> 21 trading, 60 -> 41 (at 252/365). The forecast is
    annualized regardless of horizon, so this only sets the averaging window.
    """
    return max(1, round(dte * trading_days / _CALENDAR_DAYS))


# ── Daily realized-variance proxy + HAR components ─────────────────────


def daily_variance(close: pd.Series) -> pd.Series:
    """Per-day realized-variance proxy = squared daily log return (first NaN)."""
    return log_returns(close) ** 2


def har_components(variance: pd.Series) -> pd.DataFrame:
    """Daily / weekly / monthly trailing-mean variance components for HAR.

    Each column is the trailing mean of the daily variance over the daily (1),
    weekly (5) and monthly (22) windows, all ending at ``t`` (so they use only
    information available at ``t``). NaN until each window is primed.
    """
    return pd.DataFrame(
        {
            "vd": variance.rolling(_LAG_DAILY, min_periods=_LAG_DAILY).mean(),
            "vw": variance.rolling(_LAG_WEEKLY, min_periods=_LAG_WEEKLY).mean(),
            "vm": variance.rolling(_LAG_MONTHLY, min_periods=_LAG_MONTHLY).mean(),
        }
    )


def forward_mean_variance(variance: pd.Series, horizon: int) -> pd.Series:
    """Average daily variance over the *next* ``horizon`` days (NaN at the tail).

    ``y_t = mean(variance[t+1 .. t+horizon])`` — the HAR regression target. The
    last ``horizon`` rows are NaN (no future), which is exactly the row we then
    forecast out of sample.
    """
    trailing = variance.rolling(window=horizon, min_periods=horizon).mean()
    return trailing.shift(-horizon)


# ── HAR-RV fit + forecast ──────────────────────────────────────────────


@dataclass(frozen=True)
class HarFit:
    """A fitted HAR-RV model and its one-step forward-vol forecast."""

    horizon_trading_days: int
    coef_const: float
    coef_daily: float
    coef_weekly: float
    coef_monthly: float
    r2: float
    n_obs: int
    forecast_rv: float  # annualized vol, decimal


def fit_har(
    close: pd.Series,
    horizon_trading_days: int,
    *,
    trading_days: int = _TRADING_DAYS,
    min_obs: int = _MIN_HAR_OBS,
) -> HarFit | None:
    """Fit HAR-RV by OLS and forecast annualized forward vol over the horizon.

    Returns ``None`` when there are fewer than ``min_obs`` aligned training rows
    (features + a realized forward target). The forecast point is the most recent
    bar with complete HAR components — its forward target is unknown, which is the
    out-of-sample estimate we want.
    """
    close = pd.to_numeric(close, errors="coerce")
    variance = daily_variance(close)
    feats = har_components(variance)
    target = forward_mean_variance(variance, horizon_trading_days)

    train = feats.assign(y=target).dropna()
    if len(train) < min_obs:
        return None

    design = np.column_stack(
        [np.ones(len(train)), train["vd"], train["vw"], train["vm"]]
    )
    y = train["y"].to_numpy()
    beta, *_ = np.linalg.lstsq(design, y, rcond=None)

    resid = y - design @ beta
    ss_res = float(resid @ resid)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    # Forecast from the latest fully-primed component row.
    primed = feats.dropna()
    x_last = np.array([1.0, *primed.iloc[-1][["vd", "vw", "vm"]].to_numpy()])
    pred_var = float(x_last @ beta)
    pred_var = max(pred_var, 0.0)  # OLS can dip negative on quiet tapes
    forecast_rv = float(np.sqrt(pred_var * trading_days))

    return HarFit(
        horizon_trading_days=horizon_trading_days,
        coef_const=float(beta[0]),
        coef_daily=float(beta[1]),
        coef_weekly=float(beta[2]),
        coef_monthly=float(beta[3]),
        r2=r2,
        n_obs=len(train),
        forecast_rv=forecast_rv,
    )


# ── EWMA baseline ──────────────────────────────────────────────────────


def ewma_variance(close: pd.Series, *, lam: float = _DEFAULT_LAMBDA) -> pd.Series:
    """RiskMetrics EWMA of daily variance, seeded with the first squared return.

    ``var_t = lam * var_{t-1} + (1 - lam) * r_t^2``. Indexed like the dropped-NaN
    return series; empty if there are no returns.
    """
    rets = log_returns(close).dropna()
    if rets.empty:
        return pd.Series(dtype=float)
    sq = (rets**2).to_numpy()
    out = np.empty_like(sq)
    out[0] = sq[0]
    for t in range(1, len(sq)):
        out[t] = lam * out[t - 1] + (1.0 - lam) * sq[t]
    return pd.Series(out, index=rets.index)


def forecast_ewma_rv(
    close: pd.Series,
    *,
    lam: float = _DEFAULT_LAMBDA,
    trading_days: int = _TRADING_DAYS,
) -> float | None:
    """Annualized forward vol from the latest EWMA variance (flat-forward)."""
    var = ewma_variance(close, lam=lam)
    if var.empty:
        return None
    return float(np.sqrt(float(var.iloc[-1]) * trading_days))


# ── Combined per-horizon forecast ──────────────────────────────────────


@dataclass(frozen=True)
class VolForecast:
    """Forward-vol forecasts for one horizon (annualized, decimal)."""

    horizon_dte: int
    horizon_trading_days: int
    har_rv: float | None
    ewma_rv: float | None
    har_r2: float | None
    n_obs: int


def forecast_vol(
    close: pd.Series,
    *,
    horizons: tuple[int, ...] = _DEFAULT_HORIZONS,
    trading_days: int = _TRADING_DAYS,
    lam: float = _DEFAULT_LAMBDA,
    min_obs: int = _MIN_HAR_OBS,
) -> dict[int, VolForecast]:
    """Forward-RV forecasts per calendar-day horizon.

    HAR is fit per horizon (the averaging window differs); the EWMA baseline is
    horizon-independent (flat-forward) so the same value is attached to each.
    Returns a dict keyed by the calendar-day horizon.
    """
    ewma_rv = forecast_ewma_rv(close, lam=lam, trading_days=trading_days)
    out: dict[int, VolForecast] = {}
    for dte in horizons:
        h = dte_to_trading_days(dte, trading_days=trading_days)
        fit = fit_har(close, h, trading_days=trading_days, min_obs=min_obs)
        out[dte] = VolForecast(
            horizon_dte=dte,
            horizon_trading_days=h,
            har_rv=fit.forecast_rv if fit else None,
            ewma_rv=ewma_rv,
            har_r2=fit.r2 if fit else None,
            n_obs=fit.n_obs if fit else 0,
        )
    return out
