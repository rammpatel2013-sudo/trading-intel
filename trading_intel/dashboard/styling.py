"""Shared colour-coding for dashboard tables and metrics.

Pure mappings from a descriptive state to a hex colour, used by Streamlit pages
(pandas ``Styler`` background colours, metric deltas, captions). Greens read as
"stabilising / carry / rich-side", reds as "amplifying / stress", amber as
"transitional / near a regime boundary". Colours encode the descriptive regime
only — never a buy/sell call (FlashAlpha rule 4).
"""

from __future__ import annotations

import pandas as pd

# Palette (dark-theme friendly, matches the existing Plotly charts).
GREEN = "#2ecc71"
RED = "#e74c3c"
AMBER = "#f39c12"
NEUTRAL = "#7f8c8d"
BLUE = "#3498db"


def gex_dir_color(direction: str | None) -> str:
    """Green for positive/up GEX (dealers stabilising), red for negative/down."""
    d = str(direction or "").lower()
    if d in {"up", "positive", "+", "long"}:
        return GREEN
    if d in {"down", "negative", "-", "short"}:
        return RED
    return NEUTRAL


def gamma_regime_color(regime: str | None) -> str:
    """Green = long/positive gamma (mean-reverting), red = short/negative (amplifying)."""
    r = str(regime or "").lower()
    if "long" in r or "positive" in r or "above" in r:
        return GREEN
    if "short" in r or "negative" in r or "below" in r:
        return RED
    return NEUTRAL


def zone_color(zone: str | None) -> str:
    """VEGA/VIX zone: low carry = green, mid fragility = amber, high stress = red."""
    z = str(zone or "").lower()
    return {"low": GREEN, "mid": AMBER, "high": RED}.get(z, NEUTRAL)


def richness_color(score: float | None) -> str:
    """0..100 richness percentile: >=80 rich (amber-sell), <=20 cheap (blue-buy)."""
    if score is None or pd.isna(score):
        return NEUTRAL
    if score >= 80:
        return AMBER
    if score <= 20:
        return BLUE
    return NEUTRAL


def staleness_color(state: str | None) -> str:
    """Freshness state -> colour: fresh green, stale red, unknown neutral."""
    return {"fresh": GREEN, "stale": RED}.get(str(state or "").lower(), NEUTRAL)


def flip_distance_pct(spot: float | None, flip: float | None) -> float | None:
    """Signed distance of spot from the GEX flip, as a fraction of spot.

    Positive = spot above the flip (typically the long-gamma / stabilising side);
    negative = below (short-gamma / amplifying side). ``None`` if either is
    missing or spot is non-positive.
    """
    if spot is None or flip is None or pd.isna(spot) or pd.isna(flip) or spot <= 0:
        return None
    return (float(spot) - float(flip)) / float(spot)


def flip_state(spot: float | None, flip: float | None, *, near_pct: float = 0.005) -> str:
    """Descriptive read of spot vs the GEX flip.

    Within ``near_pct`` of the flip → "near flip (regime can convert)"; otherwise
    "above flip" / "below flip". ``"n/a"`` if the distance can't be computed.
    """
    dist = flip_distance_pct(spot, flip)
    if dist is None:
        return "n/a"
    if abs(dist) <= near_pct:
        return "near flip (regime can convert)"
    return "above flip" if dist > 0 else "below flip"


def flip_proximity_color(spot: float | None, flip: float | None, *, near_pct: float = 0.005) -> str:
    """Amber when spot is near the flip (regime can convert), else neutral."""
    dist = flip_distance_pct(spot, flip)
    if dist is None:
        return NEUTRAL
    return AMBER if abs(dist) <= near_pct else NEUTRAL
