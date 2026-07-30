"""Per-symbol skew + walls + near-money strike-IV grid from an options chain.

Pure transforms over a normalized chain frame (as ``CVForgeClient.chain`` /
``ConvexClient.chain`` produce): 25Δ risk-reversal, the call/put gamma walls, and
the near-money per-strike IV grid that a day-over-day diff turns into the
fixed-strike "offered vs bid" footprint (the "a wall is not a wall" read). No
vendor/DB dependency → unit-testable. Descriptor only (FlashAlpha rule 4).

Chain columns used (all optional/None-safe): ``opt_kind, strike, expiration,
delta, iv, gxoi``.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd


def _prep(chain: pd.DataFrame, ref: date | None) -> pd.DataFrame:
    df = chain.copy()
    df["_side"] = df["opt_kind"].astype(str).str.upper().str[0]
    df["_strike"] = pd.to_numeric(df.get("strike"), errors="coerce")
    df["_iv"] = pd.to_numeric(df.get("iv"), errors="coerce")
    df["_delta"] = pd.to_numeric(df.get("delta"), errors="coerce")
    df["_gxoi"] = pd.to_numeric(df.get("gxoi"), errors="coerce")
    exp = pd.to_datetime(df.get("expiration"), errors="coerce")
    ref_ts = pd.Timestamp(ref or date.today())
    df["_dte"] = (exp - ref_ts).dt.days
    return df


def _target_expiry(df: pd.DataFrame, target_dte: int) -> float | None:
    """The available DTE closest to ``target_dte`` among still-live expiries."""
    live = df.loc[df["_dte"].notna() & (df["_dte"] >= 1), "_dte"]
    if live.empty:
        return None
    dtes = np.unique(live.to_numpy())
    return float(dtes[np.argmin(np.abs(dtes - target_dte))])


def risk_reversal_25(df: pd.DataFrame, target_dte: int) -> tuple[float | None, float | None]:
    """25Δ risk-reversal (put IV − call IV) at the ~``target_dte`` expiry.

    Positive = downside puts richer than calls (fear). Returns ``(rr25, dte)``;
    ``(None, dte)`` if either 25Δ wing is missing.
    """
    tgt = _target_expiry(df, target_dte)
    if tgt is None:
        return None, None
    exp_df = df[df["_dte"] == tgt]
    calls = exp_df[(exp_df["_side"] == "C") & exp_df["_delta"].notna() & exp_df["_iv"].notna()]
    puts = exp_df[(exp_df["_side"] == "P") & exp_df["_delta"].notna() & exp_df["_iv"].notna()]
    if calls.empty or puts.empty:
        return None, tgt
    c = calls.iloc[int((calls["_delta"] - 0.25).abs().to_numpy().argmin())]
    p = puts.iloc[int((puts["_delta"] + 0.25).abs().to_numpy().argmin())]
    return float(p["_iv"] - c["_iv"]), tgt


def gamma_walls(df: pd.DataFrame, wall_dte_max: int) -> tuple[float | None, float | None]:
    """Call/put gamma walls = the strike with the most gamma-OI per side (≤ dte cap)."""
    near = df[
        df["_dte"].notna()
        & (df["_dte"] >= 0)
        & (df["_dte"] <= wall_dte_max)
        & df["_gxoi"].notna()
        & df["_strike"].notna()
    ]
    if near.empty:
        return None, None
    cw = near[near["_side"] == "C"].groupby("_strike")["_gxoi"].sum()
    pw = near[near["_side"] == "P"].groupby("_strike")["_gxoi"].sum()
    call_wall = float(cw.idxmax()) if not cw.empty and cw.max() > 0 else None
    put_wall = float(pw.idxmax()) if not pw.empty and pw.max() > 0 else None
    return call_wall, put_wall


def near_money_strike_iv(
    df: pd.DataFrame, spot: float, target_dte: int, *, band: float = 0.12
) -> dict | None:
    """Per-strike mean IV within ``band`` of spot at the ~``target_dte`` expiry.

    Keyed by strike (string) so it round-trips through JSON; a later day's diff
    of this grid at the SAME strikes IS the fixed-strike footprint.
    """
    tgt = _target_expiry(df, target_dte)
    if tgt is None or not spot:
        return None
    exp_df = df[
        (df["_dte"] == tgt)
        & df["_strike"].notna()
        & df["_iv"].notna()
        & ((df["_strike"] - spot).abs() <= band * spot)
    ]
    if exp_df.empty:
        return None
    grid = exp_df.groupby("_strike")["_iv"].mean()
    return {f"{float(k):g}": round(float(v), 5) for k, v in grid.items()}


def sector_extras(
    chain: pd.DataFrame,
    spot: float,
    *,
    ref: date | None = None,
    target_dte: int = 30,
    wall_dte_max: int = 60,
    band: float = 0.12,
) -> dict:
    """All Layer-2 chain descriptors for one symbol: rr25 + walls + strike-IV grid."""
    if chain is None or getattr(chain, "empty", True):
        return {"rr25": None, "rr25_dte": None, "call_wall": None, "put_wall": None, "strike_iv": None}
    df = _prep(chain, ref)
    rr, rr_dte = risk_reversal_25(df, target_dte)
    call_wall, put_wall = gamma_walls(df, wall_dte_max)
    return {
        "rr25": rr,
        "rr25_dte": None if rr_dte is None else int(rr_dte),
        "call_wall": call_wall,
        "put_wall": put_wall,
        "strike_iv": near_money_strike_iv(df, spot, target_dte, band=band),
    }


def fixed_strike_footprint(today: dict | None, prior: dict | None, *, tol: float = 0.0005) -> dict:
    """Day-over-day fixed-strike vol read: at each shared strike, is IV bid or offered?

    OFFERED (IV falling) at/around a wall → the level tends to HOLD (dealers
    selling vol into it); BID (IV rising) → the level tends to BREAK (crash bid /
    short-gamma). Returns counts + a net read so the report can label wall
    conviction. ``pending`` until two days of ``strike_iv`` grids exist.
    """
    if not today or not prior:
        return {"pending": True, "offered": 0, "bid": 0, "flat": 0, "read": None}
    offered = bid = flat = 0
    for k, iv_now in today.items():
        iv_prev = prior.get(k)
        if iv_prev is None:
            continue
        d = iv_now - iv_prev
        if d < -tol:
            offered += 1
        elif d > tol:
            bid += 1
        else:
            flat += 1
    n = offered + bid + flat
    if n == 0:
        return {"pending": True, "offered": 0, "bid": 0, "flat": 0, "read": None}
    read = "offered — levels tend to HOLD" if offered > bid else "bid — levels tend to BREAK" if bid > offered else "mixed"
    return {"pending": False, "offered": offered, "bid": bid, "flat": flat, "read": read}
