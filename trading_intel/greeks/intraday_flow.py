"""Volume-weighted intraday 0DTE/1DTE exposures.

For short-dated options the open-interest snapshot understates what dealers are
actually hedging: 0DTE OI turns over inside the session, so the *traded volume*
is the better weight. This module mirrors the locked exposure formulas in
``greeks/exposures.py`` but weights each greek by traded contract volume instead
of OI, and restricts the chain to the 0DTE/1DTE tenor where gamma/charm cluster.

It produces both an aggregate read and a per-strike breakdown, and supports two
volume bases:

* **cumulative** — Convex ``day_volume`` as reported (total session flow);
* **interval** — the increment vs the previous intraday snapshot (fresh flow in
  the last cycle), via :func:`interval_volume`.

Formulas (volume analogues of the OI exposures; see exposures.py):

    gamma_vol = Σ  sign · gamma · volume      (sign = +1 calls, -1 puts)
    delta_vol = Σ  delta · volume             (delta already carries its sign)
    vanna_vol = Σ  vanna · volume · spot · iv
    charm_vol = Σ  charm · volume · spot · 365

FlashAlpha rule (CLAUDE.md rule 4): regime descriptors only — no signals.
"""

from __future__ import annotations

from datetime import date, datetime, time

import numpy as np
import pandas as pd

from trading_intel.errors import ComputationError

_SIGN = {"C": 1.0, "P": -1.0}
_REQUIRED = ("opt_kind", "strike", "gamma", "delta", "vanna", "charm", "iv", "volume")
_KEY = ("expiry", "strike", "opt_kind")

# US equity/index regular trading hours (Eastern); the scheduler runs in ET.
_MARKET_OPEN = time(9, 30)
_MARKET_CLOSE = time(16, 0)


def _sign_series(chain: pd.DataFrame) -> pd.Series:
    sign = chain["opt_kind"].astype(str).str.upper().str[0].map(_SIGN)
    if sign.isna().any():
        bad = sorted(chain.loc[sign.isna(), "opt_kind"].astype(str).unique())
        raise ComputationError(f"Unrecognized opt_kind values in chain: {bad}")
    return sign


def is_market_hours(now: datetime) -> bool:
    """True if ``now`` (assumed ET) is a regular weekday trading session."""
    if now.weekday() >= 5:  # Sat/Sun
        return False
    return _MARKET_OPEN <= now.time() <= _MARKET_CLOSE


def dte_days(expiration: pd.Series, ref: date) -> pd.Series:
    """Calendar days-to-expiry for a normalized ``expiration`` datetime column."""
    exp = pd.to_datetime(expiration, errors="coerce")
    return (exp.dt.normalize() - pd.Timestamp(ref).normalize()).dt.days


def filter_0dte_1dte(
    chain: pd.DataFrame, *, ref: date | None = None, max_dte: int = 1
) -> pd.DataFrame:
    """Keep only rows expiring within ``max_dte`` calendar days (default 0/1 DTE).

    Adds an integer ``dte`` column. Empty / column-less input returns empty.
    """
    if chain is None or chain.empty or "expiration" not in chain.columns:
        return chain if chain is not None else pd.DataFrame()
    ref = ref or date.today()
    df = chain.copy()
    df["dte"] = dte_days(df["expiration"], ref)
    return df[(df["dte"] >= 0) & (df["dte"] <= max_dte)].reset_index(drop=True)


def filter_delta_band(
    chain: pd.DataFrame, *, lo: float = 0.30, hi: float = 0.70
) -> pd.DataFrame:
    """Keep only rows whose ``|delta|`` is within ``[lo, hi]`` (near-the-money band).

    Drops far-OTM (``|delta| < lo``) and deep-ITM (``|delta| > hi``) strikes — the
    gamma that matters for the live GEX view sits near the money. Empty /
    column-less input returns empty.
    """
    if chain is None or chain.empty or "delta" not in chain.columns:
        return chain if chain is not None else pd.DataFrame()
    df = chain.copy()
    absd = pd.to_numeric(df["delta"], errors="coerce").abs()
    return df[absd.between(lo, hi)].reset_index(drop=True)


def _prepared(chain: pd.DataFrame, volume_col: str) -> tuple[pd.DataFrame, pd.Series]:
    missing = [c for c in _REQUIRED if c not in chain.columns]
    if volume_col not in chain.columns:
        missing.append(volume_col)
    if missing:
        raise ComputationError(f"Intraday chain missing required columns: {missing}")
    df = chain.copy()
    for col in ("gamma", "delta", "vanna", "charm", "iv", "strike", volume_col):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    sign = _sign_series(df)
    return df, sign


def volume_weighted_by_strike(
    chain: pd.DataFrame, spot: float, *, volume_col: str = "volume"
) -> pd.DataFrame:
    """Per-strike volume-weighted gamma/delta/vanna/charm exposures.

    Returns a frame with ``strike, gamma_vol, delta_vol, vanna_vol, charm_vol``
    summed across both sides and all kept expiries, ascending by strike. Empty
    chain → empty frame (no error).
    """
    if chain is None or chain.empty:
        return pd.DataFrame(columns=["strike", "gamma_vol", "delta_vol", "vanna_vol", "charm_vol"])
    if not np.isfinite(spot) or spot <= 0:
        raise ComputationError(f"Invalid spot for intraday exposures: {spot!r}")
    df, sign = _prepared(chain, volume_col)
    vol = df[volume_col]
    df = df.assign(
        gamma_vol=sign * df["gamma"] * vol,
        delta_vol=df["delta"] * vol,
        vanna_vol=df["vanna"] * vol * spot * df["iv"],
        charm_vol=df["charm"] * vol * spot * 365.0,
    )
    grouped = (
        df.groupby("strike", as_index=False)[
            ["gamma_vol", "delta_vol", "vanna_vol", "charm_vol"]
        ]
        .sum()
        .sort_values("strike")
        .reset_index(drop=True)
    )
    return grouped


def volume_weighted_exposures(
    chain: pd.DataFrame, spot: float, *, volume_col: str = "volume"
) -> dict:
    """Aggregate volume-weighted exposures for an intraday 0DTE/1DTE chain.

    Returns ``{gamma_vol, delta_vol, vanna_vol, charm_vol, total_volume}``.
    Empty chain → ``{}``.
    """
    if chain is None or chain.empty:
        return {}
    per_strike = volume_weighted_by_strike(chain, spot, volume_col=volume_col)
    vol = (
        pd.to_numeric(chain[volume_col], errors="coerce").fillna(0.0)
        if volume_col in chain
        else pd.Series(0.0)
    )
    return {
        "gamma_vol": float(per_strike["gamma_vol"].sum()),
        "delta_vol": float(per_strike["delta_vol"].sum()),
        "vanna_vol": float(per_strike["vanna_vol"].sum()),
        "charm_vol": float(per_strike["charm_vol"].sum()),
        "total_volume": float(np.sum(vol)),
    }


def interval_volume(curr: pd.DataFrame, prev: pd.DataFrame | None) -> pd.DataFrame:
    """Per-contract freshly-traded volume vs the previous snapshot.

    Joins ``curr`` to ``prev`` on (expiry, strike, opt_kind) and returns ``curr``
    with an added ``volume_interval`` column = ``max(curr.volume - prev.volume,
    0)``. Contracts with no prior match (newly in range) get ``volume_interval``
    = NaN (unknown), so aggregations can treat them as 0 without overstating
    fresh flow. When ``prev`` is None/empty, every interval is NaN.
    """
    if curr is None or curr.empty:
        return curr if curr is not None else pd.DataFrame()
    out = curr.copy()
    out["volume"] = pd.to_numeric(out["volume"], errors="coerce")
    if prev is None or prev.empty:
        out["volume_interval"] = np.nan
        return out
    keys = [k for k in _KEY if k in out.columns and k in prev.columns]
    prior = prev[[*keys, "volume"]].copy()
    prior["volume"] = pd.to_numeric(prior["volume"], errors="coerce")
    prior = prior.rename(columns={"volume": "_prev_volume"})
    merged = out.merge(prior, on=keys, how="left")
    delta = merged["volume"] - merged["_prev_volume"]
    merged["volume_interval"] = delta.clip(lower=0.0)
    return merged.drop(columns=["_prev_volume"])
