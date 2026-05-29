"""Forward gamma / charm field: how the per-strike field evolves as time passes.

Holds the current option positions fixed (sticky-strike, spot fixed at now) and
advances *time* across a grid, recomputing each option's Black-Scholes gamma or
charm at the shrinking time-to-expiry, then sums the sign-weighted dealer
exposure by strike. The result is a strike x future-time matrix — the smooth
"future half" of an OptionsDepth-style gamma/charm map. Recompute is sanctioned
for this simulated view (ADR-002); descriptive, not a signal (rule 4).

0DTE options are treated as expiring at the 16:00 ET cash close, so gamma sharpens
to a spike at ATM and charm collapses to zero exactly at the close.
"""
from __future__ import annotations

from datetime import datetime, time

import numpy as np
import pandas as pd

from trading_intel.greeks.black_scholes import bs_charm, bs_gamma

_SIGN = {"C": 1.0, "P": -1.0}
_DEFAULT_MULTIPLIER = 100.0
_MIN_T = 1.0 / (365.0 * 24.0 * 60.0)  # floor T at ~1 minute to avoid /0 at expiry
_SECONDS_PER_YEAR = 365.0 * 24 * 3600
_CLOSE = time(16, 0)
_NEEDED = {"opt_kind", "strike", "iv", "oi", "expiration"}


def _opt_kind_to_sign(series: pd.Series) -> pd.Series:
    return series.astype(str).str.upper().str[0].map(_SIGN)


def _expiry_datetimes(expiration: pd.Series) -> pd.Series:
    """Expiration -> expiry datetime at the 16:00 close (so 0DTE T->0 at the close)."""
    dates = pd.to_datetime(expiration, errors="coerce")
    return dates.dt.normalize() + pd.Timedelta(hours=_CLOSE.hour, minutes=_CLOSE.minute)


def _years_to(expiry_dt: np.ndarray, at: datetime) -> np.ndarray:
    delta = (expiry_dt - np.datetime64(pd.Timestamp(at))) / np.timedelta64(1, "s")
    return np.maximum(delta / _SECONDS_PER_YEAR, _MIN_T)


def forward_field(
    chain: pd.DataFrame,
    spot: float,
    *,
    greek: str,
    times: list[datetime],
    risk_free_rate: float = 0.04,
) -> pd.DataFrame:
    """Strike (index) x future-time (columns) net signed dealer ``greek`` field.

    ``greek`` is ``"gamma"`` (dollar-gamma per 1% move) or ``"charm"`` (charm x OI).
    Each column is one future timestamp; spot is held at ``spot`` and each option
    keeps its IV (sticky-strike). Empty frame on bad/empty input.
    """
    if chain is None or chain.empty or not _NEEDED.issubset(chain.columns):
        return pd.DataFrame()
    if not np.isfinite(spot) or spot <= 0 or not times:
        return pd.DataFrame()
    if greek not in ("gamma", "charm"):
        raise ValueError(f"unknown greek: {greek!r}")

    df = chain.copy()
    sign = _opt_kind_to_sign(df["opt_kind"])
    strike = pd.to_numeric(df["strike"], errors="coerce")
    sigma = pd.to_numeric(df["iv"], errors="coerce")
    oi = pd.to_numeric(df["oi"], errors="coerce")
    if "multiplier" in df.columns:
        mult = pd.to_numeric(df["multiplier"], errors="coerce")
        mult = mult.where(mult > 0, _DEFAULT_MULTIPLIER).fillna(_DEFAULT_MULTIPLIER)
    else:
        mult = pd.Series(_DEFAULT_MULTIPLIER, index=df.index)
    expiry_dt = _expiry_datetimes(df["expiration"])

    valid = (
        sign.notna() & strike.notna() & (strike > 0)
        & sigma.notna() & (sigma > 0) & oi.notna() & expiry_dt.notna()
    )
    if not valid.any():
        return pd.DataFrame()

    sign_a = sign[valid].to_numpy(dtype=float)
    strike_a = strike[valid].to_numpy(dtype=float)
    sigma_a = sigma[valid].to_numpy(dtype=float)
    oi_a = oi[valid].to_numpy(dtype=float)
    mult_a = mult[valid].to_numpy(dtype=float)
    expiry_a = expiry_dt[valid].to_numpy()

    cols: dict[datetime, pd.Series] = {}
    for t in times:
        years = _years_to(expiry_a, t)
        if greek == "gamma":
            g = bs_gamma(spot, strike_a, sigma_a, years, risk_free_rate)
            exposure = sign_a * g * oi_a * mult_a * spot**2 * 0.01
        else:
            c = bs_charm(spot, strike_a, sigma_a, years, risk_free_rate)
            exposure = sign_a * c * oi_a * mult_a
        col = pd.DataFrame({"strike": strike_a, "_e": exposure})
        cols[t] = col.groupby("strike")["_e"].sum()

    matrix = pd.DataFrame(cols).sort_index().fillna(0.0)
    return matrix


def session_close_grid(
    now: datetime, *, step_minutes: int = 10, close: time = _CLOSE
) -> list[datetime]:
    """Future timestamps from ``now`` (rounded up to the step) to the 16:00 close."""
    close_dt = now.replace(hour=close.hour, minute=close.minute, second=0, microsecond=0)
    if now >= close_dt:
        return [now.replace(second=0, microsecond=0)]
    base = pd.Timestamp(now).floor("min")
    rem = base.minute % step_minutes
    start = base if rem == 0 else base + pd.Timedelta(minutes=step_minutes - rem)
    close_ts = pd.Timestamp(close_dt)
    grid: list[datetime] = []
    t = start
    while t < close_ts:
        grid.append(t.to_pydatetime())
        t = t + pd.Timedelta(minutes=step_minutes)
    grid.append(close_dt)
    return grid
