"""Pure systematic-flow descriptors — RV path -> vol-sensitive buying/selling force.

Turns a realized-vol level (and the projected roll-off path from
``prices/realized_vol.rv_rolloff_projection``) into the mechanical exposure change
of vol-sensitive systematic funds, per the cohort assumptions in ``registry.py``.

The core exposure function is inverse-vol with a cap::

    w(rv) = clip(target_vol / rv, 0, w_max)

so exposure rises as realized vol falls. Its sensitivity is CONVEX::

    dw/drv = -target_vol / rv**2

i.e. the lower vol already is, the more the cohort buys per additional tick down —
which is why the last leg of a vol-down move carries the strongest systematic bid.
Feeding the projected RV roll-off path in gives the Δexposure schedule; scaling by
assumed cohort AUM gives an order-of-magnitude $ buying figure.

``overwriter_call_supply`` is the name-level companion: post-earnings call
overwriters re-sell calls (supply-led ΔOI: OI up + IV down, per the
``oi-flow-direction`` rule), rebuilding the call wall that becomes the re-entry
target.

Pure transforms, no I/O. Regime descriptor only (FlashAlpha rule 4) — the $ scale
is an ESTIMATE (see ``registry.py``); consume as a percentile, not a hard number.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from trading_intel.flows.registry import FundCohort


def vol_control_exposure(rv: float, *, target_vol: float, w_max: float) -> float:
    """Inverse-vol exposure weight ``clip(target_vol / rv, 0, w_max)``."""
    if not np.isfinite(rv) or rv <= 0:
        return float(w_max)  # vanishing vol -> pinned at the cap
    return float(min(max(target_vol / rv, 0.0), w_max))


def exposure_convexity(rv: float, *, target_vol: float) -> float:
    """Signed sensitivity ``dw/drv = -target_vol / rv**2`` (more negative = buys harder)."""
    if not np.isfinite(rv) or rv <= 0:
        return 0.0
    return float(-target_vol / (rv * rv))


@dataclass(frozen=True, slots=True)
class FlowEstimate:
    """One cohort's projected mechanical flow over the RV roll-off horizon.

    ``buying_usd`` > 0 is net BUYING pressure into the underlying (falling vol ->
    add exposure); < 0 is selling. ``d_exposure`` is the terminal exposure change
    in leverage units; ``convexity`` is ``dw/drv`` at the current RV (how hard the
    next tick down bites). All $ are order-of-magnitude (see ``registry.py``).
    """

    cohort: str
    rv_today: float
    rv_terminal: float
    w_today: float
    w_terminal: float
    d_exposure: float
    buying_usd: float
    convexity: float
    trend_sign: float


def cohort_flow(
    cohort: FundCohort,
    rv_today: float,
    rv_path: Sequence[float],
    *,
    aum_usd: float | None = None,
    trend_sign: float = 1.0,
) -> FlowEstimate:
    """Projected mechanical flow for one cohort over an RV roll-off path.

    Args:
        cohort: the cohort assumptions (``registry.FundCohort``).
        rv_today: current trailing realized vol (decimal).
        rv_path: projected RV levels over the horizon (decimal), e.g.
            ``rv_rolloff_projection(...)["projected_rv"]``. The terminal value
            drives the exposure change.
        aum_usd: override the cohort's AUM (e.g. from ``Settings``); defaults to
            ``cohort.aum_usd``.
        trend_sign: +1/-1 trend direction for trend-gated (CTA) cohorts. A
            trend-gated cohort only adds exposure in the trend's direction, so its
            buying is multiplied by ``trend_sign``; non-gated cohorts ignore it.

    Returns a ``FlowEstimate``.
    """
    aum = float(cohort.aum_usd if aum_usd is None else aum_usd)
    path = [float(r) for r in rv_path if r is not None and np.isfinite(r)]
    rv_terminal = path[-1] if path else float(rv_today)
    w0 = vol_control_exposure(rv_today, target_vol=cohort.target_vol, w_max=cohort.w_max)
    wt = vol_control_exposure(rv_terminal, target_vol=cohort.target_vol, w_max=cohort.w_max)
    d_exposure = wt - w0
    gate = float(trend_sign) if cohort.trend_gated else 1.0
    buying_usd = d_exposure * aum * gate
    return FlowEstimate(
        cohort=cohort.name,
        rv_today=float(rv_today),
        rv_terminal=float(rv_terminal),
        w_today=float(w0),
        w_terminal=float(wt),
        d_exposure=float(d_exposure),
        buying_usd=float(buying_usd),
        convexity=exposure_convexity(rv_today, target_vol=cohort.target_vol),
        trend_sign=gate,
    )


def aggregate_systematic_buying(estimates: Sequence[FlowEstimate]) -> dict:
    """Sum cohort flows into one systematic buying-pressure read.

    Returns ``total_buying_usd`` (net across cohorts), ``by_cohort`` ($ each), and
    ``direction`` (``buying``/``selling``/``flat``). The magnitude is order-of-
    magnitude; rank it cross-sectionally / bank it forward for a usable percentile.
    """
    by_cohort = {e.cohort: e.buying_usd for e in estimates}
    total = float(sum(by_cohort.values()))
    if total > 0:
        direction = "buying"
    elif total < 0:
        direction = "selling"
    else:
        direction = "flat"
    return {
        "total_buying_usd": total,
        "by_cohort": by_cohort,
        "direction": direction,
        "n_cohorts": len(by_cohort),
    }


@dataclass(frozen=True, slots=True)
class CallStrikeChange:
    """One above-spot call strike's day-over-day OI/IV change (for supply reads)."""

    strike: float
    d_oi: float  # ΔOI: >0 opening, <0 closing (sign only — never direction)
    d_iv: float  # ΔIV: pairs with ΔOI to tell demand-led (buy) vs supply-led (write)
    gxoi: float = 0.0  # current gamma-OI at the strike (wall magnitude)


def overwriter_call_supply(
    changes: Sequence[CallStrikeChange],
    *,
    min_d_oi: float = 0.0,
) -> dict:
    """Detect post-earnings call-overwriter re-supply rebuilding the call wall.

    Supply-led writing = OI opening (``d_oi > 0``) with IV softening (``d_iv < 0``)
    — the ``oi-flow-direction`` rule (call-OI↑ + IV↓ = writing, NOT bullish). The
    strike with the largest supply-led opening above spot is the call wall the
    overwriters are rebuilding — the upside magnet for the re-entry.

    Returns ``supply_led`` (any qualifying strike), ``rebuild_strike``,
    ``rebuild_d_oi``, ``rebuild_gxoi``, and ``n_supply_strikes``.
    """
    supply = [
        c
        for c in changes
        if c.d_oi is not None
        and c.d_iv is not None
        and np.isfinite(c.d_oi)
        and np.isfinite(c.d_iv)
        and c.d_oi > min_d_oi
        and c.d_iv < 0
    ]
    if not supply:
        return {
            "supply_led": False,
            "rebuild_strike": None,
            "rebuild_d_oi": 0.0,
            "rebuild_gxoi": 0.0,
            "n_supply_strikes": 0,
        }
    top = max(supply, key=lambda c: c.d_oi)
    return {
        "supply_led": True,
        "rebuild_strike": float(top.strike),
        "rebuild_d_oi": float(top.d_oi),
        "rebuild_gxoi": float(top.gxoi),
        "n_supply_strikes": len(supply),
    }
