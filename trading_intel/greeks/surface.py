"""Implied-volatility surface construction from a normalized options chain.

Pure transform layer (no vendor imports): takes a chain DataFrame + spot and
builds a regular moneyness x tenor grid of implied vol, suitable for 3D surface
plotting and (later) constant-maturity / sticky-strike analysis.

Each expiry's listed strikes are interpolated onto a common moneyness grid so
the result is a clean rectangular matrix. Regime descriptor only (FlashAlpha
rule 4) — emits no signals.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from trading_intel.errors import ComputationError

_REQUIRED = ("expiration", "strike", "opt_kind", "iv")


@dataclass
class VolSurface:
    """A rectangular implied-vol surface on a moneyness x tenor grid."""

    moneyness: np.ndarray  # 1d (M,) — strike / spot
    dte: np.ndarray  # 1d (T,) — calendar days to expiry, ascending
    iv: np.ndarray  # 2d (T, M) — implied vol (decimal); NaN where unobserved
    spot: float
    ref: date

    @property
    def n_expiries(self) -> int:
        return int(self.dte.shape[0])


def build_surface_grid(
    chain: pd.DataFrame,
    spot: float,
    *,
    moneyness_range: tuple[float, float] = (0.80, 1.20),
    moneyness_steps: int = 41,
    max_dte: int = 365,
    min_strikes_per_expiry: int = 5,
    ref: date | None = None,
) -> VolSurface:
    """Build a regular moneyness x tenor IV grid from a wide options chain.

    Args:
        chain: normalized chain; needs ``expiration`` (datetime), ``strike``,
            ``opt_kind`` (call/put), ``iv`` (decimal).
        spot: underlying price (defines moneyness = strike / spot).
        moneyness_range / moneyness_steps: the common x-grid each expiry is
            interpolated onto.
        max_dte: drop expiries beyond this horizon.
        min_strikes_per_expiry: skip thin expiries (can't interpolate reliably).
        ref: reference date for DTE (default today).
    """
    if chain is None or chain.empty:
        raise ComputationError("Empty chain: cannot build vol surface")
    missing = [c for c in _REQUIRED if c not in chain.columns]
    if missing:
        raise ComputationError(f"Chain missing columns for surface: {missing}")
    if not spot or spot <= 0:
        raise ComputationError(f"Invalid spot for surface: {spot!r}")

    ref_ts = pd.Timestamp(ref or date.today()).normalize()
    df = chain.copy()
    df["iv"] = pd.to_numeric(df["iv"], errors="coerce")
    df["strike"] = pd.to_numeric(df["strike"], errors="coerce")
    exp = pd.to_datetime(df["expiration"], errors="coerce")
    df["_dte"] = (exp.dt.normalize() - ref_ts).dt.days
    df["_moneyness"] = df["strike"] / spot

    lo, hi = moneyness_range
    mask = (
        df["iv"].notna()
        & (df["iv"] > 0)
        & (df["iv"] < 5)  # drop absurd IVs (data glitches)
        & df["strike"].notna()
        & df["_moneyness"].between(lo, hi)
        & df["_dte"].notna()
        & (df["_dte"] >= 0)
        & (df["_dte"] <= max_dte)
    )
    df = df[mask]
    if df.empty:
        raise ComputationError("No chain rows in the requested moneyness/DTE window")

    # Average call+put IV at each (expiry, strike) node (parity -> ~equal IVs).
    node = df.groupby(["_dte", "strike"], as_index=False).agg(
        iv=("iv", "mean"), moneyness=("_moneyness", "first")
    )

    target_m = np.linspace(lo, hi, moneyness_steps)
    dtes: list[int] = []
    rows: list[np.ndarray] = []
    for dte_val, g in node.groupby("_dte", sort=True):
        if len(g) < min_strikes_per_expiry:
            continue
        g = g.sort_values("moneyness")
        # interpolate onto the common grid; NaN outside the observed strike range
        interp = np.interp(
            target_m,
            g["moneyness"].to_numpy(),
            g["iv"].to_numpy(),
            left=np.nan,
            right=np.nan,
        )
        dtes.append(int(dte_val))
        rows.append(interp)

    if not rows:
        raise ComputationError("No expiries with enough strikes to build a surface")

    return VolSurface(
        moneyness=target_m,
        dte=np.array(dtes, dtype=int),
        iv=np.vstack(rows),
        spot=float(spot),
        ref=(ref or date.today()),
    )


# ── Delta-space surface (matches the dashboard's delta axis) ────────────────

# Standard OTM delta grid (percent): 5Δ (far OTM) .. 50Δ (ATM).
DEFAULT_DELTAS: tuple[float, ...] = (5, 7.5, 10, 15, 20, 25, 30, 35, 40, 45, 47.5, 50)


@dataclass
class DeltaSurface:
    """IV interpolated onto a fixed |delta| grid, per expiry, per wing.

    ``iv_put`` / ``iv_call`` are (T, D): one row per selected expiry, one column
    per delta in ``deltas`` (ascending, 5..50). 50Δ ~ ATM. Regime descriptor.
    """

    deltas: np.ndarray  # (D,) delta % grid, ascending
    dte: np.ndarray  # (T,) days to expiry, ascending
    expiries: list[date]
    iv_put: np.ndarray  # (T, D)
    iv_call: np.ndarray  # (T, D)
    spot: float
    ref: date

    @property
    def n_expiries(self) -> int:
        return int(self.dte.shape[0])

    @property
    def atm_iv(self) -> np.ndarray:
        """(T,) ATM IV per expiry = mean of put/call IV at the 50Δ grid point."""
        idx = int(np.argmax(self.deltas))
        return np.nanmean(np.vstack([self.iv_put[:, idx], self.iv_call[:, idx]]), axis=0)


def _interp_delta(group: pd.DataFrame, grid: np.ndarray) -> np.ndarray:
    g = group.sort_values("_absdelta")
    return np.interp(grid, g["_absdelta"].to_numpy(), g["iv"].to_numpy(), left=np.nan, right=np.nan)


def build_delta_surface(
    chain: pd.DataFrame,
    *,
    deltas: tuple[float, ...] = DEFAULT_DELTAS,
    n_expiries: int = 3,
    min_strikes_per_side: int = 4,
    ref: date | None = None,
) -> DeltaSurface:
    """Interpolate IV onto a fixed |delta| grid for the n nearest liquid expiries.

    Needs ``expiration`` (datetime), ``opt_kind`` (call/put), ``delta``, ``iv``.
    Spot is read from the chain if a ``spot`` column exists, else NaN (the delta
    surface does not need it; callers can pass spot separately for display).
    """
    if chain is None or chain.empty:
        raise ComputationError("Empty chain: cannot build delta surface")
    for col in ("expiration", "opt_kind", "delta", "iv"):
        if col not in chain.columns:
            raise ComputationError(f"Chain missing column for delta surface: {col!r}")

    ref_ts = pd.Timestamp(ref or date.today()).normalize()
    df = chain.copy()
    df["iv"] = pd.to_numeric(df["iv"], errors="coerce")
    df["delta"] = pd.to_numeric(df["delta"], errors="coerce")
    exp = pd.to_datetime(df["expiration"], errors="coerce")
    df["_exp_date"] = exp.dt.normalize()
    df["_dte"] = (df["_exp_date"] - ref_ts).dt.days
    df["_absdelta"] = df["delta"].abs() * 100.0
    df["_side"] = np.where(df["delta"] < 0, "put", "call")

    grid = np.array(sorted(deltas), dtype=float)
    lo, hi = grid.min(), grid.max()
    mask = (
        df["iv"].notna()
        & (df["iv"] > 0)
        & (df["iv"] < 5)
        & df["_dte"].notna()
        & (df["_dte"] >= 0)
        & df["_absdelta"].between(lo - 5, hi + 5)  # small pad so 5Δ/50Δ interpolate
    )
    df = df[mask]
    if df.empty:
        raise ComputationError("No usable rows for delta surface")

    # Choose the nearest expiries that have enough points on BOTH wings.
    chosen: list[tuple[int, pd.Timestamp]] = []
    for dte_val, g in df.groupby("_dte", sort=True):
        puts = g[g["_side"] == "put"]
        calls = g[g["_side"] == "call"]
        if len(puts) >= min_strikes_per_side and len(calls) >= min_strikes_per_side:
            chosen.append((int(dte_val), g["_exp_date"].iloc[0]))
        if len(chosen) >= n_expiries:
            break
    if not chosen:
        raise ComputationError("No expiry has enough strikes on both wings")

    dtes: list[int] = []
    expiries: list[date] = []
    put_rows: list[np.ndarray] = []
    call_rows: list[np.ndarray] = []
    for dte_val, exp_ts in chosen:
        g = df[df["_dte"] == dte_val]
        put_rows.append(_interp_delta(g[g["_side"] == "put"], grid))
        call_rows.append(_interp_delta(g[g["_side"] == "call"], grid))
        dtes.append(dte_val)
        expiries.append(exp_ts.date())

    spot_val = float("nan")
    if "spot" in chain.columns:
        s = pd.to_numeric(chain["spot"], errors="coerce").dropna()
        if not s.empty:
            spot_val = float(s.iloc[0])

    return DeltaSurface(
        deltas=grid,
        dte=np.array(dtes, dtype=int),
        expiries=expiries,
        iv_put=np.vstack(put_rows),
        iv_call=np.vstack(call_rows),
        spot=spot_val,
        ref=(ref or date.today()),
    )


def forward_vol(dte: np.ndarray, atm_iv: np.ndarray) -> np.ndarray:
    """Forward vol between consecutive expiries via variance additivity.

    fwd_var[i] = (iv[i]^2 * T[i] - iv[i-1]^2 * T[i-1]) / (T[i] - T[i-1]); fwd[0] = iv[0].
    Returns NaN where forward variance goes negative (calendar-arb / data noise).
    """
    t = np.asarray(dte, dtype=float) / 365.0
    v = np.asarray(atm_iv, dtype=float)
    var = v**2 * t
    fwd = np.full_like(v, np.nan)
    if v.size:
        fwd[0] = v[0]
    for i in range(1, v.size):
        dt = t[i] - t[i - 1]
        if dt > 0:
            fv = (var[i] - var[i - 1]) / dt
            fwd[i] = np.sqrt(fv) if fv > 0 else np.nan
    return fwd
