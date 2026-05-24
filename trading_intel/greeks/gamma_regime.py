"""Gamma-regime classifier from a per-strike gamma-OI chain.

Composes three existing descriptors into one regime read:
  - net signed GEX (calls +, puts -), matching the ConvexValue app,
  - the gamma flip point (greeks.flip_point.gex_flip), and
  - the gamma walls (greeks.walls.compute_walls),
into a label: "positive" (long-gamma: dealer hedging dampens moves, mean-
reverting, price pins toward walls), "negative" (short-gamma: hedging amplifies
moves, trending, gaps run), or "transitional" (spot sitting on the flip, regime
unstable). Pure transform; reuses sibling modules. Regime descriptor only -
FlashAlpha rule 4, no signals. Needs >= 1 snapshot (unlike the decomposition,
which needs two days).
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from trading_intel.errors import ComputationError
from trading_intel.greeks.flip_point import gex_flip
from trading_intel.greeks.walls import compute_walls

_SIGN = {"C": 1.0, "P": -1.0}
_REQUIRED = ("opt_kind", "strike", "gxoi")
#: Within this fraction of spot from the flip, the regime is called transitional.
NEAR_FLIP_FRAC = 0.005


def net_gex(chain: pd.DataFrame) -> float:
    """Net signed gamma-OI (calls +, puts -). Raises on missing columns."""
    if chain is None or chain.empty or not {"opt_kind", "gxoi"}.issubset(chain.columns):
        raise ComputationError("chain needs opt_kind + gxoi for net GEX")
    sign = chain["opt_kind"].astype(str).str.upper().str[0].map(_SIGN).fillna(0.0)
    gxoi = pd.to_numeric(chain["gxoi"], errors="coerce").fillna(0.0)
    return float((gxoi * sign).sum())


def classify(
    net_gex_value: float,
    spot: float,
    flip: float | None,
    *,
    near_flip_frac: float = NEAR_FLIP_FRAC,
) -> tuple[str, float | None]:
    """Pure label from inputs. Returns ``(regime, distance_to_flip_pct)``.

    Transitional when spot is within ``near_flip_frac`` of the flip; otherwise
    the regime follows the sign of net GEX (positive => long-gamma).
    """
    dist_pct: float | None = None
    if flip is not None and spot > 0:
        dist_pct = abs(spot - flip) / spot * 100.0
        if abs(spot - flip) / spot < near_flip_frac:
            return "transitional", dist_pct
    if net_gex_value > 0:
        return "positive", dist_pct
    if net_gex_value < 0:
        return "negative", dist_pct
    return "transitional", dist_pct


@dataclass(frozen=True)
class GammaRegime:
    """A one-snapshot gamma-regime descriptor."""

    regime: str  # "positive" | "negative" | "transitional"
    net_gex: float
    spot: float
    flip: float | None
    distance_to_flip_pct: float | None
    call_wall: float | None
    put_wall: float | None

    def regime_read(self) -> str:
        if self.regime == "transitional":
            where = (
                f"~{self.distance_to_flip_pct:.2f}% from the flip"
                if self.distance_to_flip_pct is not None
                else "net gamma near zero"
            )
            return (
                f"Transitional ({where}): spot on the gamma flip - regime unstable, "
                "can tip either way; lower conviction."
            )
        if self.regime == "positive":
            return (
                "Positive-gamma regime: hedging dampens moves - mean-reverting, "
                "range-bound, price tends to pin toward the walls."
            )
        return (
            "Negative-gamma regime: hedging amplifies moves - trending, breakouts "
            "run and gaps tend not to fill."
        )


def classify_gamma_regime(
    chain: pd.DataFrame, spot: float, *, near_flip_frac: float = NEAR_FLIP_FRAC
) -> GammaRegime:
    """Classify the gamma regime for one snapshot of one symbol.

    ``chain`` is a normalized per-strike frame; ``net_gex`` needs ``opt_kind`` +
    ``gxoi``, the flip additionally uses ``strike/iv/oi/expiration``, and the
    walls use ``strike/opt_kind/gxoi``. Missing flip inputs => ``flip`` is None
    and the regime falls back to the net-GEX sign.
    """
    if chain is None or chain.empty or not set(_REQUIRED).issubset(chain.columns):
        raise ComputationError(f"chain missing required columns {_REQUIRED}")
    ng = net_gex(chain)
    flip = gex_flip(chain, spot)
    try:
        walls = compute_walls(chain)
    except ComputationError:
        walls = {"call_wall": None, "put_wall": None}
    regime, dist = classify(ng, spot, flip, near_flip_frac=near_flip_frac)
    return GammaRegime(
        regime=regime,
        net_gex=ng,
        spot=float(spot),
        flip=flip,
        distance_to_flip_pct=dist,
        call_wall=walls.get("call_wall"),
        put_wall=walls.get("put_wall"),
    )
