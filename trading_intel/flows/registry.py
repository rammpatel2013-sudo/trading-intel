"""Systematic vol-sensitive fund cohorts - reference assumptions for the flow proxy.

The "buying force" that keys off realized vol comes from funds that size exposure
INVERSELY to vol: vol-control / target-vol products, and the inverse-vol sizing
inside CTAs and risk-parity. When realized vol falls they lever up and buy; when
it rises they de-lever and sell. This registry holds the per-cohort assumptions
the descriptor math needs (``flows/descriptors.py``).

NOTE: THESE ARE ESTIMATES, not measured positioning. AUM and target-vol conventions
are third-party desk estimates (Nomura/McElligott, GS, DB regularly publish them)
and vary widely; the estimator window is a modelling choice. Every row is
``verify=True``. The flow $ output is therefore ORDER-OF-MAGNITUDE - consume it as
a cross-sectional / banked percentile (cf. the ``no-ibkr-api`` percentile stance),
not as a hard dollar figure. Config knobs (``VOL_CONTROL_AUM``, ``VOL_TARGET``,
``CTA_AUM``, ``RISK_PARITY_AUM``) override these defaults at the composition root.

Reference data only - not persisted, not a vendor call. Regime descriptor input
(FlashAlpha rule 4) - never a signal on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CohortKind = Literal["vol_control", "cta", "risk_parity"]

#: Estimator conventions the cohort uses for its realized-vol reading.
#:   rv_21     -> 1-month (~21 trading day) trailing realized vol. The standard
#:               vol-control / target-vol convention - desk notes describe VCFs as
#:               sizing off "1m trailing RV" (Yamco 07/20, McElligott). This is what
#:               the index-level flow tool actually computes (rv_rolloff_projection
#:               window=21), so it is the honest label for the vol_control cohort.
#:   max_20_60 -> max(20d, 60d) realized (de-risk fast, re-risk slow).
#:   ewm_94    -> RiskMetrics EWMA (lambda 0.94).
#:   rv_63     -> a slower ~quarter window (risk-parity-like).
Estimator = Literal["rv_21", "max_20_60", "ewm_94", "rv_63"]

#: Trailing window (sessions) for the single-window estimators, for when a cohort's
#: RV is computed directly. rv_21 / rv_63 map to their window; max_20_60 / ewm_94 are
#: multi-window conventions the index-level flow tool currently approximates with the
#: shared 21d roll-off (per-cohort RV differentiation is a later enhancement).
ESTIMATOR_WINDOW: dict[str, int] = {"rv_21": 21, "rv_63": 63}


@dataclass(frozen=True, slots=True)
class FundCohort:
    """One vol-sensitive systematic cohort's flow assumptions.

    ``aum_usd``      estimated assets that size inversely to vol (dollars).
    ``target_vol``   the vol the cohort targets (decimal, e.g. 0.10 = 10%).
    ``w_max``        exposure cap (leverage ceiling; 1.0 = fully invested, no lever).
    ``estimator``    which realized-vol window the cohort keys off.
    ``trend_gated``  True for CTAs: buying only in the direction of the trend
                     (falling vol amplifies an existing trend, it does not create
                     one).
    ``verify``       always True here - calibrate before trusting the $ scale.
    """

    name: str
    kind: CohortKind
    aum_usd: float
    target_vol: float
    w_max: float
    estimator: Estimator
    trend_gated: bool = False
    verify: bool = True


# Default cohort assumptions (order-of-magnitude; calibrate against current desk
# estimates before trusting absolute $). Deliberately conservative mid-points.
_ROWS: tuple[FundCohort, ...] = (
    FundCohort("vol_control", "vol_control", 350e9, 0.10, 1.5, "rv_21"),
    FundCohort("cta", "cta", 300e9, 0.12, 2.0, "ewm_94", trend_gated=True),
    FundCohort("risk_parity", "risk_parity", 150e9, 0.10, 1.5, "rv_63"),
)

REGISTRY: dict[str, FundCohort] = {c.name: c for c in _ROWS}


def cohorts() -> tuple[FundCohort, ...]:
    """All registered cohorts, in a stable order."""
    return _ROWS


def cohort_for(name: str) -> FundCohort | None:
    """Reference row for a cohort by name (case-insensitive); ``None`` if unknown."""
    return REGISTRY.get(name.strip().lower())
