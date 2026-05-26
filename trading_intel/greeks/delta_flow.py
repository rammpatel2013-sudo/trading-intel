"""Traded delta-notional flow: call vs put, all expiries vs the next expiry.

The cumulative-delta-notional view (price overlaid with the running dollar delta
of the day's option flow). For each option, the traded delta notional is

    delta * volume * spot * multiplier        (multiplier = 100)

where ``volume`` is the cumulative session volume (so the per-snapshot sum is
already the running cumulative line). Summed by side it gives the call line
(positive — call delta > 0) and the put line (negative — put delta < 0). We split
the chain two ways:

- **all** — every expiry in the chain (the "All Trades" series);
- **next** — only the nearest expiry (the "Next Expiry" series).

Pure transform (chain frame + spot in, numbers out), unit-tested. Descriptive
flow read-through — what actually traded, never a signal (FlashAlpha rule 4).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

MULTIPLIER = 100  # standard US equity-option contract multiplier


@dataclass(frozen=True)
class DeltaFlowSplit:
    """Call/put traded delta-notional, for all expiries and the next expiry."""

    call_notional_all: float
    put_notional_all: float
    call_notional_next: float
    put_notional_next: float
    next_expiry: date | None


def _prepared(chain: pd.DataFrame) -> pd.DataFrame | None:
    """Normalize the chain to ``side`` (C/P), numeric ``delta``/``volume``, ``_exp``."""
    if chain is None or chain.empty:
        return None
    df = chain.copy()
    kind_col = "opt_kind" if "opt_kind" in df.columns else "cp"
    exp_col = "expiration" if "expiration" in df.columns else "expiry"
    if not {kind_col, exp_col, "delta", "volume"}.issubset(df.columns):
        return None
    df["_side"] = df[kind_col].astype(str).str.upper().str[0]
    df["_delta"] = pd.to_numeric(df["delta"], errors="coerce")
    df["_volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0)
    df["_exp"] = pd.to_datetime(df[exp_col], errors="coerce")
    df = df[df["_side"].isin(["C", "P"]) & df["_delta"].notna() & df["_exp"].notna()]
    return df if not df.empty else None


def _side_notional(df: pd.DataFrame, spot: float) -> tuple[float, float]:
    """(call_notional, put_notional) = sum(delta * volume) * spot * MULTIPLIER by side."""
    contrib = df["_delta"] * df["_volume"] * float(spot) * MULTIPLIER
    call = float(contrib[df["_side"] == "C"].sum())
    put = float(contrib[df["_side"] == "P"].sum())
    return call, put


def delta_notional_split(chain: pd.DataFrame, spot: float | None) -> DeltaFlowSplit | None:
    """Compute the call/put delta-notional split (all expiries vs next expiry).

    Returns ``None`` if the chain is empty/unusable or ``spot`` is invalid.
    """
    if spot is None or not np.isfinite(spot) or spot <= 0:
        return None
    df = _prepared(chain)
    if df is None:
        return None

    call_all, put_all = _side_notional(df, spot)
    next_exp = df["_exp"].min()
    near = df[df["_exp"] == next_exp]
    call_next, put_next = _side_notional(near, spot)

    return DeltaFlowSplit(
        call_notional_all=call_all,
        put_notional_all=put_all,
        call_notional_next=call_next,
        put_notional_next=put_next,
        next_expiry=next_exp.date() if pd.notna(next_exp) else None,
    )
