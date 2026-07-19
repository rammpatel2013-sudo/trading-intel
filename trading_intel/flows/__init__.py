"""Systematic vol-sensitive flow descriptors — RV/HV/IV -> buying force.

The descriptor layer that turns realized-vol dynamics into the mechanical
exposure change of vol-sensitive systematic funds (vol-control / target-vol, plus
CTA and risk-parity inverse-vol sizing), and the name-level overwriter call-supply
read that rebuilds the call wall post-earnings.

``registry`` carries the per-cohort AUM / target-vol / estimator assumptions (all
ESTIMATES — calibrate); ``descriptors`` carries the pure math. Feed the RV
roll-off path from ``prices/realized_vol.rv_rolloff_projection``.

Regime descriptor only (FlashAlpha rule 4) — the $ scale is order-of-magnitude;
consume as a percentile, never a signal on its own.
"""

from __future__ import annotations

from trading_intel.flows.descriptors import (
    CallStrikeChange,
    FlowEstimate,
    aggregate_systematic_buying,
    cohort_flow,
    exposure_convexity,
    overwriter_call_supply,
    vol_control_exposure,
)
from trading_intel.flows.registry import REGISTRY, FundCohort, cohort_for, cohorts

__all__ = [
    "REGISTRY",
    "CallStrikeChange",
    "FlowEstimate",
    "FundCohort",
    "aggregate_systematic_buying",
    "cohort_flow",
    "cohort_for",
    "cohorts",
    "exposure_convexity",
    "overwriter_call_supply",
    "vol_control_exposure",
]
