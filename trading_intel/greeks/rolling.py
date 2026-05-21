"""Long-dated (rolling) GEX — total + per-expiration term structure.

Given a wide options chain (many expirations), compute net signed gxoi summed
across every expiration within a horizon (default ~6 months / 180 days), plus a
per-expiration breakdown so the term structure can be tracked over time.

Units match the near-term snapshot: raw net signed gxoi (calls +, puts −),
consistent with the ConvexValue app. Regime descriptor only (FlashAlpha rule 4).
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from trading_intel.errors import ComputationError

_SIGN = {"C": 1.0, "P": -1.0}
_REQUIRED = ("opt_kind", "expiration", "gxoi")


def compute_rolling_gex(
    chain: pd.DataFrame,
    *,
    window_days: int = 180,
    ref: date | None = None,
) -> dict:
    """Aggregate long-dated net gxoi within ``window_days``.

    Args:
        chain: normalized chain spanning many expirations. Needs
            ``opt_kind, expiration (datetime), gxoi``.
        window_days: horizon in calendar days (default 180 ≈ 6 months).
        ref: reference date for DTE (default today).

    Returns ``{"total": float, "n_expirations": int, "term": [...]}`` where each
    term entry is ``{"expiration": date, "dte": int, "gex": float}`` sorted by
    expiration. Empty chain → zeros / empty term list.
    """
    if chain is None or chain.empty:
        return {"total": 0.0, "n_expirations": 0, "term": []}

    missing = [c for c in _REQUIRED if c not in chain.columns]
    if missing:
        raise ComputationError(f"Rolling GEX chain missing columns: {missing}")

    ref_ts = pd.Timestamp(ref or date.today())
    df = chain.copy()
    df["gxoi"] = pd.to_numeric(df["gxoi"], errors="coerce").fillna(0.0)

    sign = df["opt_kind"].astype(str).str.upper().str[0].map(_SIGN)
    if sign.isna().any():
        bad = sorted(df.loc[sign.isna(), "opt_kind"].astype(str).unique())
        raise ComputationError(f"Unrecognized opt_kind values: {bad}")
    df["_signed"] = df["gxoi"] * sign

    exp = pd.to_datetime(df["expiration"], errors="coerce")
    df["_exp_date"] = exp.dt.normalize()
    df["_dte"] = (exp - ref_ts).dt.days

    mask = df["_dte"].notna() & (df["_dte"] >= 0) & (df["_dte"] <= window_days)
    df = df[mask]
    if df.empty:
        return {"total": 0.0, "n_expirations": 0, "term": []}

    term: list[dict] = []
    for exp_date, g in df.groupby("_exp_date", sort=True):
        term.append({
            "expiration": exp_date.date(),
            "dte": int(g["_dte"].iloc[0]),
            "gex": float(g["_signed"].sum()),
        })

    return {
        "total": float(df["_signed"].sum()),
        "n_expirations": len(term),
        "term": term,
    }
