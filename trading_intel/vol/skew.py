"""Per-name volatility-skew descriptors (RR / BF / term-structure / percentile).

Pure functions over a ``greeks.surface.DeltaSurface``. Produces the FX-convention
surface coordinates — ATM IV, risk reversal (RR), butterfly (BF) — at the
institutional 25Δ and the tail-anchoring 10Δ, plus their term-structure and the
trailing-distribution percentile a name's read is most usefully expressed in.

Conventions:

- **Risk reversal** here follows the equity convention ``iv_put_Δ - iv_call_Δ``.
  Positive RR = the structural put-skew bid (the "smirk"); a negative print means
  calls are richer than the same-delta puts — the rare "call bias" state the MU
  reference chart shows. *Note:* FX flips the sign (call - put); we standardize
  on the equity convention because the entire downstream pipeline (signals,
  dashboard, AM summary) reads in equity-bias terms.

- **Butterfly** = ``(iv_put_Δ + iv_call_Δ)/2 - iv_atm`` (wing convexity vs the
  ATM anchor) — the third FX-convention coordinate alongside ATM and RR.

- **Percentile** is the fraction of trailing observations ≤ the current read; we
  reuse ``vol.richness.percentile_rank`` so the cold-start contract is shared
  with VRP/IV-rank (under ``MIN_HISTORY`` → ``None``, never a misleading score).

- **Shift vs Slide** classifies a one-day surface move per the project playbook
  ``docs/playbooks/from-doc-s-mailbox-volatility-shift-vs-slide-what-s-the-difference.md``:
  a "shift" is an ATM-level move with skew unchanged; a "slide" is the opposite.

Skew is signal-eligible (ADR-003 revision 2). The signal generators live in
``strategies/skew.py``; this module is pure math and label production.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from trading_intel.greeks.surface import DeltaSurface
from trading_intel.vol.richness import percentile_rank
from trading_intel.vol.term_skew import (
    SKEW_INVERTED_PTS,
    SKEW_MODERATE_PTS,
    SKEW_STEEP_PTS,
)

#: Default percentile windows (trading days). 63d ≈ MenthorQ's 3-month surface,
#: 252d ≈ one year — short for regime context, long for tail-of-distribution.
PCTILE_WINDOW_SHORT = 63
PCTILE_WINDOW_LONG = 252

#: Tail-of-distribution thresholds for ``extreme_label``. The MU reference read
#: was a 0.0161 3-month percentile, so 0.05 is a safe boundary for "tail".
EXTREME_LO = 0.05
EXTREME_HI = 0.95

#: Shift-vs-slide thresholds. The day's surface move is dominated by the larger
#: of |ΔATM| (vol points) and |ΔRR| (vol points); below both bands it's noise.
SHIFT_ATM_BAND_PTS = 0.5
SLIDE_RR_BAND_PTS = 0.5

#: Extreme call-bias label band (vol points). The standard skew labels in
#: ``vol.term_skew.classify_skew`` cover steep / moderate / flat / inverted; we
#: extend the inverted region with an "extreme call bias" tier (≤ -2 vol pts).
RR_EXTREME_CALL_BIAS_PTS = -2.0


# ── Surface coordinate readouts ────────────────────────────────────────


def _expiry_index(surface: DeltaSurface, horizon_dte: int) -> int | None:
    """Index of the expiry whose DTE is closest to ``horizon_dte``.

    ``None`` when the surface has no expiries — callers short-circuit to NaN.
    """
    if surface.n_expiries == 0:
        return None
    return int(np.argmin(np.abs(surface.dte - horizon_dte)))


def _delta_index(surface: DeltaSurface, delta: float) -> int:
    """Index of the delta-grid column closest to the requested |Δ| (in percent)."""
    return int(np.argmin(np.abs(surface.deltas - float(delta))))


def risk_reversal(
    surface: DeltaSurface, *, delta: float = 25.0, horizon_dte: int = 30
) -> float | None:
    """Risk reversal ``iv_put_Δ - iv_call_Δ`` at the expiry nearest ``horizon_dte``.

    Returns ``None`` if either wing did not interpolate (NaN in the surface) or
    if the surface has no expiries; the caller is then responsible for the
    cold-row behavior.
    """
    j = _expiry_index(surface, horizon_dte)
    if j is None:
        return None
    i = _delta_index(surface, delta)
    rr = float(surface.iv_put[j, i] - surface.iv_call[j, i])
    return rr if np.isfinite(rr) else None


def butterfly(
    surface: DeltaSurface, *, delta: float = 25.0, horizon_dte: int = 30
) -> float | None:
    """Butterfly ``(iv_put_Δ + iv_call_Δ)/2 - iv_atm`` at the nearest expiry.

    ATM IV per expiry is sourced from ``DeltaSurface.atm_iv``; ``None`` when the
    ATM did not interpolate or the wings are NaN.
    """
    j = _expiry_index(surface, horizon_dte)
    if j is None:
        return None
    i = _delta_index(surface, delta)
    put = float(surface.iv_put[j, i])
    call = float(surface.iv_call[j, i])
    atm = float(surface.atm_iv[j])
    if not (np.isfinite(put) and np.isfinite(call) and np.isfinite(atm)):
        return None
    return (put + call) / 2.0 - atm


def skew_term_curve(
    surface: DeltaSurface, *, delta: float = 25.0
) -> list[tuple[int, float]]:
    """RR across every available expiry as ``[(dte, rr), …]``, ascending in DTE.

    Skips any expiry whose wing IVs did not interpolate. Empty list if the
    surface has no usable expiries.
    """
    out: list[tuple[int, float]] = []
    i = _delta_index(surface, delta)
    for j, dte in enumerate(surface.dte):
        rr = float(surface.iv_put[j, i] - surface.iv_call[j, i])
        if np.isfinite(rr):
            out.append((int(dte), rr))
    return out


def front_back_slope(
    surface: DeltaSurface,
    *,
    delta: float = 25.0,
    near_dte: int = 30,
    far_dte: int = 180,
) -> float | None:
    """Front-month RR minus back-end RR (in vol points).

    Positive = skew steeper at the front than the back (the usual equity shape);
    near-zero or inverted = a flattening-out of the smirk's term structure.
    ``None`` if either tenor's RR is unavailable.
    """
    near = risk_reversal(surface, delta=delta, horizon_dte=near_dte)
    far = risk_reversal(surface, delta=delta, horizon_dte=far_dte)
    if near is None or far is None:
        return None
    return near - far


# ── Standardization ────────────────────────────────────────────────────


def skew_percentile(
    history: Sequence[float], current: float, *, min_history: int = 20
) -> float | None:
    """Fraction of trailing ``history`` ≤ ``current`` (0..1). ``None`` if cold.

    Thin wrapper over ``vol.richness.percentile_rank`` so RR / BF percentiles use
    the same cold-start contract as VRP and IV-rank.
    """
    return percentile_rank(history, float(current), min_history=min_history)


# ── Descriptive labels ─────────────────────────────────────────────────


def classify_rr(rr_pts: float | None) -> str | None:
    """Descriptive RR state in vol points (equity convention: ``put - call``).

    Tiers extend ``vol.term_skew.classify_skew`` by adding an "extreme call bias"
    band for the strongly-negative-RR regimes the project's reference charts show
    (e.g. MU at -7 vol pts).
    """
    if rr_pts is None or not np.isfinite(rr_pts):
        return None
    if rr_pts >= SKEW_STEEP_PTS:
        return "steep put bid (crash-protection demand)"
    if rr_pts >= SKEW_MODERATE_PTS:
        return "moderate put bid"
    if rr_pts <= RR_EXTREME_CALL_BIAS_PTS:
        return "extreme call bias (upside-chasing skew)"
    if rr_pts <= SKEW_INVERTED_PTS:
        return "inverted (call bias)"
    return "flat"


def extreme_label(
    pctile: float | None, *, lo: float = EXTREME_LO, hi: float = EXTREME_HI
) -> str | None:
    """Tag tail-of-distribution percentiles. ``None`` when nothing extreme.

    - ``≤ lo`` → ``"tail call bias"`` (RR unusually low for this name)
    - ``≥ hi`` → ``"tail put bid"`` (RR unusually high for this name)
    """
    if pctile is None or not np.isfinite(pctile):
        return None
    if pctile <= lo:
        return "tail call bias"
    if pctile >= hi:
        return "tail put bid"
    return None


def shift_vs_slide(
    *,
    d_atm_iv_pts: float | None,
    d_rr_pts: float | None,
    atm_band: float = SHIFT_ATM_BAND_PTS,
    rr_band: float = SLIDE_RR_BAND_PTS,
) -> str | None:
    """Classify a one-day surface move as ``shift`` / ``slide`` / ``mixed`` / ``flat``.

    Inputs are the day-over-day changes in ATM IV and 25Δ RR in vol points. Both
    bands default to 0.5 vol pts (the ``vol.term_skew.FLAT_BAND_PTS`` band); a
    move below both bands is ``"flat"``. When both axes break their band, the
    label is ``"mixed"``.

    Returns ``None`` if either input is unavailable (e.g. no prior-day row to
    diff against), so callers leave the column NULL rather than guess.
    """
    if d_atm_iv_pts is None or d_rr_pts is None:
        return None
    if not (np.isfinite(d_atm_iv_pts) and np.isfinite(d_rr_pts)):
        return None
    atm_break = abs(d_atm_iv_pts) >= atm_band
    rr_break = abs(d_rr_pts) >= rr_band
    if atm_break and not rr_break:
        return "shift"
    if rr_break and not atm_break:
        return "slide"
    if atm_break and rr_break:
        return "mixed"
    return "flat"


# ── Composite RR descriptor (state + tail tag) ─────────────────────────


def compose_label(rr_pts: float | None, pctile_long: float | None) -> str:
    """Combine the RR-state label with the tail-percentile tag, if any.

    ``classify_rr`` covers state ("moderate put bid", "extreme call bias", …);
    ``extreme_label`` covers tail-of-distribution context. The composed string is
    what gets stored on the ``skew_snapshots.label`` column and rendered on the
    dashboard / AM summary.
    """
    state = classify_rr(rr_pts) or "unknown"
    tail = extreme_label(pctile_long)
    return f"{state} — {tail}" if tail else state
