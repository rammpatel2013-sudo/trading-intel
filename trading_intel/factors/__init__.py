"""Multi-factor cross-sectional scoring layer.

Growth / Quality / Value / Momentum / Risk composites over a universe, sourced
from CVForge FMP fundamentals (ADR-005 — no new vendor). Descriptive research
scores only (FlashAlpha rule 4).
"""

from __future__ import annotations

from trading_intel.factors.compute import (
    DEFAULT_WEIGHTS,
    FACTOR_DEFS,
    FACTORS,
    FactorInputs,
    FactorScores,
    compute_factor_scores,
    inputs_from_mapping,
)

__all__ = [
    "DEFAULT_WEIGHTS",
    "FACTORS",
    "FACTOR_DEFS",
    "FactorInputs",
    "FactorScores",
    "compute_factor_scores",
    "inputs_from_mapping",
]
