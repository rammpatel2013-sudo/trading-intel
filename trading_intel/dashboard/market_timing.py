"""Market-timing synthesis: combine the gamma regime + vol regime into one
descriptive market bias.

Pure (no I/O). FlashAlpha rule 4: this is a descriptive regime read, NOT a trade
signal - actual signals come only from validated strategies/ + the probability
model. It just summarizes "what kind of tape is this" from the dealer-gamma
regime, the VIX zone, and the term-structure shape.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MarketBias:
    label: str
    detail: str


def market_bias(
    gamma_regime: str | None,
    vol_zone: str | None,
    term_shape: str | None,
) -> MarketBias:
    """Descriptive bias from gamma regime + VIX zone + term-structure shape.

    Priority: a transitional gamma flip -> Mixed; clear risk-off (negative gamma
    OR backwardation OR high VIX) -> defensive; clear calm (positive gamma + low
    VIX + non-inverted curve) -> range/sellable; otherwise Mixed.
    """
    if gamma_regime == "transitional":
        return MarketBias(
            "Transitional",
            "Spot on the gamma flip - regime can tip either way; lower conviction "
            "until it resolves.",
        )

    risk_off = (
        gamma_regime == "negative"
        or term_shape == "backwardation"
        or vol_zone == "high"
    )
    if risk_off:
        return MarketBias(
            "Risk-off / trending",
            "Negative gamma / inverted curve / elevated VIX: hedging amplifies "
            "moves, trends and breakouts run and bounces are lower-confidence. "
            "Favor defense / long-vol over premium selling.",
        )

    calm = (
        gamma_regime == "positive"
        and vol_zone == "low"
        and term_shape in (None, "contango", "flat")
    )
    if calm:
        return MarketBias(
            "Calm / range-bound",
            "Positive gamma + low VIX + non-inverted curve: hedging dampens moves, "
            "mean-reverting toward the walls. Premium-selling environment; trend "
            "continuation more reliable.",
        )

    return MarketBias(
        "Mixed",
        "Regime signals are not aligned - no clear edge; lower conviction.",
    )
