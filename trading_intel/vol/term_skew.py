"""Term-structure slope, skew-vs-history, and the VEGA/VIX regime gate.

The third leg of the vol-richness scanner. Where ``richness.py`` answers "is this
name's vol rich or cheap vs forecast?", this module adds the *context* that
decides whether a rich (short-vol) read is actually safe to act on:

- **Term slope** — the 30↔60 calendar slope per name (``iv_60 - iv_30``) plus a
  read of the market VIX term structure (``vix9d/vix/vix3m/vix6m``). Contango =
  calm/carry; backwardation = front-end stress.
- **25Δ skew vs history** — the current 25-delta put skew classified
  descriptively (steep/moderate/flat/inverted) and standardized to the name's own
  trailing skew percentile (reuses ``richness.percentile_rank``).
- **VEGA/VIX regime gate** — the MANDATORY tail-risk overlay. In the VIX stress
  zone (> 32, MEMORY VEGA/VIX zones) short-vol ("rich → premium-sell") candidates
  are forced OFF: selling vol into a stress regime is the classic blow-up. Zone
  thresholds are reused from ``dashboard.vix_view`` so there is one source of
  truth.

Pure functions only (numbers/labels in, numbers/labels out). The gate operates on
``richness`` *labels* (not the row internals) so the two modules stay decoupled.

Regime descriptor only (FlashAlpha rule 4) — context for a read-through, never a
trade signal. The gate only ever makes the read MORE conservative.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from trading_intel.vol.richness import percentile_rank

# VEGA/VIX zone thresholds (MEMORY VEGA/VIX zones). Mirrored here so this pure
# analytic carries no DB/dashboard dependency; ``dashboard.vix_view`` holds the
# same constants for the view layer — keep the two in sync if they ever change.
ZONE_LOW_MAX = 22.0
ZONE_MID_MAX = 32.0
CRISIS_LEVEL = 38.3

#: Flat-band defaults: 0.5 vol pts. Decimal IV slopes pass 0.005; VIX-pt slopes 0.5.
FLAT_BAND_DECIMAL = 0.005
FLAT_BAND_PTS = 0.5

#: 25Δ put-skew descriptive thresholds, in vol points (mirrors surface_report).
SKEW_STEEP_PTS = 3.0
SKEW_MODERATE_PTS = 1.0
SKEW_INVERTED_PTS = -1.0


# ── Term-structure slope ───────────────────────────────────────────────


def term_slope(near_iv: float | None, far_iv: float | None) -> float | None:
    """Slope ``far - near`` (unit follows the inputs: decimal IV or VIX points).

    ``None`` if either leg is missing/NaN. Positive = upward (contango);
    negative = inverted (backwardation).
    """
    if near_iv is None or far_iv is None:
        return None
    if not (np.isfinite(near_iv) and np.isfinite(far_iv)):
        return None
    return float(far_iv) - float(near_iv)


def classify_slope(slope: float | None, *, flat_band: float = FLAT_BAND_DECIMAL) -> str | None:
    """Label a slope: ``contango`` / ``backwardation`` / ``flat`` (within band).

    ``flat_band`` must match the slope's units (decimal vs vol points). ``None``
    when ``slope`` is ``None``.
    """
    if slope is None:
        return None
    if abs(slope) < flat_band:
        return "flat"
    return "contango" if slope > 0 else "backwardation"


def vix_term_slope(
    vix9d: float | None, vix6m: float | None
) -> tuple[float | None, str | None]:
    """Market VIX term slope (``vix6m - vix9d``, vol pts) + its shape label."""
    slope = term_slope(vix9d, vix6m)
    return slope, classify_slope(slope, flat_band=FLAT_BAND_PTS)


# ── 25-delta skew ──────────────────────────────────────────────────────


def classify_skew(skew_pts: float | None) -> str | None:
    """Descriptive 25Δ put-skew state from vol points (``iv_put25 - iv_call25``)."""
    if skew_pts is None or not np.isfinite(skew_pts):
        return None
    if skew_pts >= SKEW_STEEP_PTS:
        return "steep (strong downside / crash-protection demand)"
    if skew_pts >= SKEW_MODERATE_PTS:
        return "moderate downside skew"
    if skew_pts <= SKEW_INVERTED_PTS:
        return "inverted (call skew / upside demand)"
    return "flat"


def skew_percentile(
    history: Sequence[float], current_skew: float
) -> float | None:
    """Where today's 25Δ skew sits in the name's own trailing distribution (0..1).

    Thin wrapper over ``richness.percentile_rank`` (cold → ``None``) so skew and
    VRP standardize identically.
    """
    return percentile_rank(history, current_skew)


# ── VEGA/VIX regime gate (mandatory tail-risk overlay) ─────────────────


def classify_zone(vix: float | None) -> str | None:
    """Map a VIX level to its VEGA/VIX regime zone (``low``/``mid``/``high``).

    Mirrors ``dashboard.vix_view.classify_zone``: carry < 22, fragility 22-32,
    stress > 32. ``None`` if the level is unknown.
    """
    if vix is None or not np.isfinite(vix):
        return None
    if vix < ZONE_LOW_MAX:
        return "low"
    if vix <= ZONE_MID_MAX:
        return "mid"
    return "high"


@dataclass(frozen=True)
class RegimeGate:
    """The market vol regime and whether short-vol reads are allowed."""

    vix: float | None
    zone: str | None  # low / mid / high / None
    short_vol_allowed: bool
    note: str


def build_regime_gate(vix_level: float | None) -> RegimeGate:
    """Build the regime gate from the current VIX level.

    Short-vol candidates are blocked in the stress zone (``> 32``). When the VIX
    is unavailable the gate stays inactive (allowed) but says so — it never
    fabricates a stress reading.
    """
    zone = classify_zone(vix_level)
    allowed = zone != "high"
    if zone == "high":
        note = (
            f"VIX stress (> {ZONE_MID_MAX:.0f}): short-vol candidates gated OFF "
            "(mandatory tail-risk overlay)."
        )
    elif zone == "mid":
        note = "VIX fragility band (22-32): short-vol allowed with caution."
    elif zone == "low":
        note = "VIX carry regime (< 22): short-vol environment."
    else:
        note = "VIX unavailable: regime gate not applied."
    return RegimeGate(
        vix=(float(vix_level) if vix_level is not None else None),
        zone=zone,
        short_vol_allowed=allowed,
        note=note,
    )


def is_short_vol_label(label: str) -> bool:
    """True if a richness label denotes a short-vol (premium-sell) candidate."""
    low = label.lower()
    return "premium-sell" in low or low.startswith("rich")


def gated_label(label: str, gate: RegimeGate) -> str:
    """Apply the regime gate to a richness label.

    Short-vol labels are annotated as gated-off when the gate disallows short vol;
    every other label (cheap/long-vol, neutral, cold) is returned unchanged — the
    overlay only ever tightens, never loosens.
    """
    if is_short_vol_label(label) and not gate.short_vol_allowed:
        return f"{label} — GATED OFF (VIX stress > {ZONE_MID_MAX:.0f})"
    return label
