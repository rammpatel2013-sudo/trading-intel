"""Spot-ladder MM dollar-gamma profile, per expiry + All Expiries (ADR-002).

Reproduces the VolSignals/VS3D-style "$Gamma vs spot reference" curve: for a grid
of hypothetical spot levels, recompute every option's Black-Scholes gamma and sum
the sign-weighted dealer dollar-gamma, grouped by expiration. The aggregate
("all") curve's zero-crossing is the gamma-flip level; below it dealers are short
gamma (move-amplifying), above it long (dampening).

**Sticky-strike**: each strike keeps its own stored IV as spot moves across the
ladder (no smile re-solve) — the convention VS3D and the project's VIX
decomposition use. Recompute is sanctioned for simulation views only (ADR-002);
Convex pre-computed greeks stay the default for snapshot/by-strike views.

Pure and unit-testable. Descriptive regime view, not a signal (rule 4).
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from trading_intel.greeks.black_scholes import dollar_gamma, years_to_expiry

_SIGN = {"C": 1.0, "P": -1.0}
_DEFAULT_MULTIPLIER = 100.0
_EPOCH_DAY_THRESHOLD = 10_000
_NEEDED = {"opt_kind", "strike", "iv", "oi", "expiration"}

ALL_COL = "all"
SPOT_COL = "spot_ref"


def _expiry_labels(expiration: pd.Series) -> pd.Series:
    """Expiration column -> ISO date strings (handles datetimes + epoch-days)."""
    if pd.api.types.is_numeric_dtype(expiration):
        numeric = pd.to_numeric(expiration, errors="coerce")
        if float(numeric.median()) >= _EPOCH_DAY_THRESHOLD:
            parsed = pd.to_datetime(numeric, unit="D", origin="unix", errors="coerce")
        else:
            parsed = pd.to_datetime(expiration, errors="coerce")
    else:
        parsed = pd.to_datetime(expiration, errors="coerce")
    return parsed.dt.date.astype("string")


def gamma_profile(
    chain: pd.DataFrame,
    spot: float,
    *,
    ref: date | None = None,
    n_points: int = 81,
    span: float = 0.07,
    risk_free_rate: float = 0.04,
) -> pd.DataFrame:
    """$Gamma by hypothetical spot, one column per expiry + ``all`` (sticky-strike).

    For each spot ``S`` on a +/-``span`` ladder of ``n_points`` around ``spot``, sums
    sign-weighted dealer dollar-gamma over every option (calls +, puts -), grouped
    by expiration. Needs ``opt_kind, strike, iv, oi, expiration`` (``multiplier``
    optional). Returns a wide frame indexed by ``spot_ref`` with per-expiry columns
    (ISO date, ascending) plus ``all``. Empty/invalid input -> empty frame.
    """
    if chain is None or chain.empty or not _NEEDED.issubset(chain.columns):
        return pd.DataFrame()
    if not np.isfinite(spot) or spot <= 0:
        return pd.DataFrame()

    df = chain.copy()
    sign = df["opt_kind"].astype(str).str.upper().str[0].map(_SIGN)
    strike = pd.to_numeric(df["strike"], errors="coerce")
    sigma = pd.to_numeric(df["iv"], errors="coerce")
    oi = pd.to_numeric(df["oi"], errors="coerce")
    if "multiplier" in df.columns:
        mult = pd.to_numeric(df["multiplier"], errors="coerce")
        mult = mult.where(mult > 0, _DEFAULT_MULTIPLIER).fillna(_DEFAULT_MULTIPLIER)
    else:
        mult = pd.Series(_DEFAULT_MULTIPLIER, index=df.index)
    years = years_to_expiry(df["expiration"], ref or date.today())
    labels = _expiry_labels(df["expiration"])

    valid = (
        sign.notna() & strike.notna() & (strike > 0)
        & sigma.notna() & (sigma > 0) & oi.notna() & np.isfinite(years) & labels.notna()
    )
    if not valid.any():
        return pd.DataFrame()

    vmask = valid.to_numpy()
    sign_a = sign[valid].to_numpy(dtype=float)
    strike_a = strike[valid].to_numpy(dtype=float)
    sigma_a = sigma[valid].to_numpy(dtype=float)
    oi_a = oi[valid].to_numpy(dtype=float)
    mult_a = mult[valid].to_numpy(dtype=float)
    years_a = years[vmask]
    labels_a = labels[valid].to_numpy()

    ladder = np.linspace(spot * (1.0 - span), spot * (1.0 + span), n_points)
    # (n_points, n_options) dealer dollar-gamma; each row is one hypothetical spot.
    dg = np.vstack([
        dollar_gamma(s, strike_a, sigma_a, years_a, oi_a, sign_a,
                     multiplier=mult_a, r=risk_free_rate)
        for s in ladder
    ])

    out = pd.DataFrame({SPOT_COL: ladder})
    for lab in sorted(set(labels_a)):
        out[lab] = dg[:, labels_a == lab].sum(axis=1)
    out[ALL_COL] = dg.sum(axis=1)
    return out.set_index(SPOT_COL)
