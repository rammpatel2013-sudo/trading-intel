"""VIX decomposition (CBOE 6-factor, "lite") from consecutive SPX skews.

Replicates the CBOE VIX Index Decomposition framework (Ed Tom, "The VIX Index
Decomposition", Aug 2025) at the representative-delta level. It splits a
day-over-day move in 30-day implied vol into the six principal components
volatility traders use to describe the skew's evolution, so a VIX move can be
read as mechanical vs. genuine repricing of risk.

Pure transform: no I/O, no vendor imports (mirrors greeks/surface.py), so it is
unit-testable against CBOE's published worked example. Factors are returned in
whatever IV units the inputs carry; feed vol points (e.g. 18.99) to get
VIX-point-comparable contributions. Convex stores IV as a decimal, so the live
wiring will scale by 100 before calling this.

Factors:
  1. sticky_strike  - ATM IV riding the PRIOR day's fixed skew as spot moved
                      (mechanical; CBOE "expected move per sticky strike").
  2. parallel_shift - whole-surface reprice at the new ATM (regime / true fear).
  3. put_gradient   - excess put-IV change at the ~30-delta shoulder beyond the
                      parallel shift (downside-hedge demand).
  4. call_gradient  - same for ~30-delta calls (upside demand).
  5. down_convexity - excess put-IV change at the ~10-delta wing beyond parallel
                      (tail-hedge demand).
  6. up_convexity   - same for ~10-delta calls (levered-upside demand).

"Lite" attribution: each shoulder/wing factor is the excess IV move at a
representative delta over the parallel shift -- matching the intermediate numbers
in CBOE's worked example. The "full" attribution (recomputing the VIX variance
strip after perturbing every strike in a delta band) is a later refinement.
Descriptive regime read only - FlashAlpha rule 4, no signals.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from trading_intel.errors import ComputationError

_REQUIRED = ("strike", "cp", "iv")
SHOULDER_DELTA = 0.30
WING_DELTA = 0.10


@dataclass(frozen=True)
class SkewSnapshot:
    """One day's OTM IV skew + per-side delta map, ready for decomposition.

    ``strikes``/``ivs`` form the OTM fixed-strike curve (puts below spot, calls
    at/above spot), ascending by strike. The per-side absolute-delta arrays are
    used to locate representative-delta strikes. Build via ``skew_from_chain``.
    """

    spot: float
    strikes: np.ndarray
    ivs: np.ndarray
    put_strikes: np.ndarray
    put_abs_deltas: np.ndarray
    call_strikes: np.ndarray
    call_abs_deltas: np.ndarray

    def iv_at(self, strike: float) -> float:
        """Linear-interpolated IV at ``strike`` on the OTM curve."""
        if self.strikes.size == 0:
            raise ComputationError("empty skew curve")
        return float(np.interp(strike, self.strikes, self.ivs))

    def strike_at_delta(self, abs_delta: float, side: str) -> float:
        """Strike at absolute delta ``abs_delta`` for ``side`` ('P' or 'C')."""
        d, k = (
            (self.put_abs_deltas, self.put_strikes)
            if side == "P"
            else (self.call_abs_deltas, self.call_strikes)
        )
        if d.size == 0:
            raise ComputationError(f"no {side} deltas to locate the {abs_delta} strike")
        order = np.argsort(d)
        return float(np.interp(abs_delta, d[order], k[order]))


def skew_from_chain(chain: pd.DataFrame, spot: float) -> SkewSnapshot:
    """Build a ``SkewSnapshot`` from a per-strike chain frame + spot.

    ``chain`` needs ``strike``/``cp``/``iv`` (and ``delta`` to locate shoulder/
    wing strikes). The OTM curve uses puts for K < spot and calls for K >= spot;
    duplicate strikes are averaged. Raises ``ComputationError`` on unusable input.
    """
    if chain is None or chain.empty or not set(_REQUIRED).issubset(chain.columns):
        raise ComputationError("chain missing required columns strike/cp/iv")
    df = chain.copy()
    df["cp"] = df["cp"].astype(str).str.upper().str[0]
    df = df[df["cp"].isin(["C", "P"])].dropna(subset=["strike", "iv"])
    if df.empty:
        raise ComputationError("chain has no usable call/put IV rows")

    puts = df[(df["cp"] == "P") & (df["strike"] < spot)][["strike", "iv"]]
    calls = df[(df["cp"] == "C") & (df["strike"] >= spot)][["strike", "iv"]]
    curve = pd.concat([puts, calls])
    if curve.empty:  # all strikes on one side of spot -> use everything
        curve = df[["strike", "iv"]]
    curve = curve.groupby("strike", as_index=False)["iv"].mean().sort_values("strike")

    has_delta = "delta" in df.columns
    pset = df[df["cp"] == "P"].dropna(subset=["delta"]) if has_delta else df.iloc[0:0]
    cset = df[df["cp"] == "C"].dropna(subset=["delta"]) if has_delta else df.iloc[0:0]

    return SkewSnapshot(
        spot=float(spot),
        strikes=curve["strike"].to_numpy(dtype=float),
        ivs=curve["iv"].to_numpy(dtype=float),
        put_strikes=pset["strike"].to_numpy(dtype=float),
        put_abs_deltas=pset["delta"].abs().to_numpy(dtype=float),
        call_strikes=cset["strike"].to_numpy(dtype=float),
        call_abs_deltas=cset["delta"].abs().to_numpy(dtype=float),
    )


@dataclass(frozen=True)
class VixDecomposition:
    """The six factor contributions of a day-over-day VIX move (input IV units)."""

    sticky_strike: float
    parallel_shift: float
    put_gradient: float
    call_gradient: float
    down_convexity: float
    up_convexity: float
    spot_prev: float
    spot_now: float

    @property
    def factors(self) -> dict[str, float]:
        return {
            "sticky_strike": self.sticky_strike,
            "parallel_shift": self.parallel_shift,
            "put_gradient": self.put_gradient,
            "call_gradient": self.call_gradient,
            "down_convexity": self.down_convexity,
            "up_convexity": self.up_convexity,
        }

    @property
    def dominant(self) -> str:
        """Name of the factor with the largest absolute contribution."""
        return max(self.factors.items(), key=lambda kv: abs(kv[1]))[0]

    def regime_read(self) -> str:
        """Plain-language mechanical-vs-fear read from the belly factors."""
        s, p = abs(self.sticky_strike), abs(self.parallel_shift)
        if s + p == 0:
            return "flat - no belly move"
        if p > s:
            direction = "up (risk-off repricing)" if self.parallel_shift > 0 else "down (fear bleeding out)"
            return f"parallel-shift dominated: genuine repricing of risk, {direction}"
        return "sticky-strike dominated: mechanical move, not new fear"


def decompose(
    prev: SkewSnapshot,
    now: SkewSnapshot,
    *,
    shoulder_delta: float = SHOULDER_DELTA,
    wing_delta: float = WING_DELTA,
) -> VixDecomposition:
    """Decompose the move from ``prev`` to ``now`` into the six CBOE factors.

    Belly factors use ATM (the prevailing spot each day); shoulder/wing factors
    use the representative-delta strikes located on the ``now`` skew, measuring
    excess IV change over the parallel shift.
    """
    sticky = prev.iv_at(now.spot) - prev.iv_at(prev.spot)
    parallel = now.iv_at(now.spot) - prev.iv_at(now.spot)

    def excess(abs_delta: float, side: str) -> float:
        k = now.strike_at_delta(abs_delta, side)
        return (now.iv_at(k) - prev.iv_at(k)) - parallel

    return VixDecomposition(
        sticky_strike=sticky,
        parallel_shift=parallel,
        put_gradient=excess(shoulder_delta, "P"),
        call_gradient=excess(shoulder_delta, "C"),
        down_convexity=excess(wing_delta, "P"),
        up_convexity=excess(wing_delta, "C"),
        spot_prev=prev.spot,
        spot_now=now.spot,
    )


def interpolate_to_30d(
    near: pd.DataFrame, far: pd.DataFrame, t1: float, t2: float, *, target: float = 30.0
) -> pd.DataFrame:
    """Variance-space interpolate two same-strike skews to a synthetic 30-day skew.

    ``near``/``far`` are ``[strike, iv]`` frames for the expiries at ``t1`` < target
    < ``t2`` calendar days (CBOE whitepaper Appendix A). Returns a ``[strike, iv]``
    frame on the strikes common to both. Preserves IV units (vol points in ->
    vol points out).
    """
    if t2 == t1:
        raise ComputationError("near and far expiries must differ")
    w1 = (t2 - target) / (t2 - t1)
    w2 = (target - t1) / (t2 - t1)
    n = near.dropna(subset=["strike", "iv"]).set_index("strike")["iv"]
    f = far.dropna(subset=["strike", "iv"]).set_index("strike")["iv"]
    common = n.index.intersection(f.index)
    if common.empty:
        raise ComputationError("near and far skews share no strikes")
    s1 = n.loc[common].to_numpy(dtype=float)
    s2 = f.loc[common].to_numpy(dtype=float)
    var30 = w1 * s1**2 * t1 + w2 * s2**2 * t2
    iv30 = np.sqrt(var30 / target)
    return (
        pd.DataFrame({"strike": common.to_numpy(dtype=float), "iv": iv30})
        .sort_values("strike")
        .reset_index(drop=True)
    )
