"""Call-wall / put-wall detection from the per-strike gamma-OI chain.

The *call wall* is the strike carrying the most call-side gamma-OI (``gxoi``) —
the level dealers defend, acting as resistance / a pin. The *put wall* is the
analogous put-side level (support). Tracking the call wall drifting up over days
reads dealer positioning loosening to the upside. Regime descriptor only — emits
no signals (FlashAlpha rule 4).

Raw ``gxoi`` is unsigned gamma*OI (both calls and puts positive; the call/put
sign is applied elsewhere for net GEX), so the wall on each side is just the
strike that maximizes summed ``gxoi`` for that side.
"""

from __future__ import annotations

import pandas as pd

from trading_intel.errors import ComputationError

_REQUIRED = ("strike", "opt_kind", "gxoi")


def compute_walls(chain: pd.DataFrame) -> dict:
    """Call & put wall for one snapshot of one symbol.

    Sums ``gxoi`` by strike within each side and picks the strike with the
    largest total. ``chain`` needs ``strike``, ``opt_kind`` (call/put), ``gxoi``
    (already filtered to the symbol/snapshot the caller wants). Returns
    ``{"call_wall", "put_wall", "call_wall_gxoi", "put_wall_gxoi"}`` (wall = None
    when a side has no strikes).
    """
    if chain is None or chain.empty:
        raise ComputationError("Empty chain: cannot compute walls")
    missing = [c for c in _REQUIRED if c not in chain.columns]
    if missing:
        raise ComputationError(f"Wall chain missing columns: {missing}")

    df = chain.copy()
    df["gxoi"] = pd.to_numeric(df["gxoi"], errors="coerce").fillna(0.0)
    df["strike"] = pd.to_numeric(df["strike"], errors="coerce")
    side = df["opt_kind"].astype(str).str.upper().str[0]

    out: dict = {}
    for label, code in (("call", "C"), ("put", "P")):
        sub = df[(side == code) & df["strike"].notna()]
        if sub.empty:
            out[f"{label}_wall"] = None
            out[f"{label}_wall_gxoi"] = 0.0
            continue
        by_strike = sub.groupby("strike")["gxoi"].sum()
        out[f"{label}_wall"] = float(by_strike.idxmax())
        out[f"{label}_wall_gxoi"] = float(by_strike.max())
    return out
