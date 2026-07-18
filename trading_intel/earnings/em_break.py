"""Expected-move-break detector — the trigger for the post-earnings gamma pattern.

Doc McGraw's durable claim (see ``docs/learning/em-break-gamma-burnoff-digest.md``):
an earnings move that breaks *beyond* the options-implied expected move tends to
over-realize (persist) until the near-term options structure that amplified it is
hedged, decayed, or expired. This module turns that into two pure, testable reads:

    em_break()        given the PRE-earnings implied range and the realized gap,
                      how far beyond the straddle did the stock move (break ratio
                      + approximate sigma) and which way.
    over_realization()  across the post-earnings window, is the cumulative move
                      still extending beyond the expected move (persisting) or
                      retracing (stalling)?

The at-the-money straddle runs about ``STRADDLE_SIGMA`` (~0.8) of a full 1-sigma
IV*sqrt(t) cone (``greeks/straddle.py``), so ``expected_move_pct`` here is the
straddle-implied range and the sigma conversion divides that out.

Pure transform, no I/O. Descriptive regime read only (FlashAlpha rule 4): it
labels how violently a name broke its implied range — it is not a signal or
advice. The signal-eligible consumer is ``strategies/em_break_reentry.py``.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

#: The ATM straddle is ~0.8 of a full 1-sigma move, so |gap| / EM ≈ sigma / 0.8.
STRADDLE_SIGMA: float = 0.8

#: break_ratio (= |gap%| / EM%) band edges -> qualitative label.
_CONTAINED = 0.8  # stayed inside the straddle
_AT_EDGE = 1.2  # brushed the edge of the implied range
_BREAK = 2.0  # cleanly beyond -> "violent" above this


def expected_move_pct(straddle: float, spot: float) -> float:
    """Straddle-implied expected move as a fraction of spot (``straddle / spot``).

    ``spot ± straddle`` is the market's compact expected-range bracket; dividing by
    spot expresses it as a percentage move so it compares to a realized gap %.
    """
    if not np.isfinite(straddle) or not np.isfinite(spot) or spot <= 0 or straddle <= 0:
        raise ValueError(f"expected_move_pct: bad straddle/spot ({straddle!r}, {spot!r})")
    return float(straddle) / float(spot)


def realized_gap_pct(pre_close: float, post_ref: float) -> float:
    """Signed realized move from the pre-earnings close to a post-earnings level.

    ``post_ref`` is whatever level defines the reaction (the first post-print
    close, or the open). Sign carries the direction (down = negative).
    """
    if not np.isfinite(pre_close) or not np.isfinite(post_ref) or pre_close <= 0:
        raise ValueError(f"realized_gap_pct: bad prices ({pre_close!r}, {post_ref!r})")
    return float(post_ref) / float(pre_close) - 1.0


def _label(break_ratio: float) -> str:
    if break_ratio < _CONTAINED:
        return "contained"
    if break_ratio < _AT_EDGE:
        return "at_edge"
    if break_ratio < _BREAK:
        return "break"
    return "violent"


def em_break(
    em_pct: float,
    gap_pct: float,
    *,
    straddle_sigma: float = STRADDLE_SIGMA,
) -> dict:
    """Classify a realized earnings gap against the pre-earnings expected move.

    Args:
        em_pct: pre-earnings straddle-implied move as a fraction of spot
            (``expected_move_pct``). Must be > 0.
        gap_pct: signed realized move as a fraction (``realized_gap_pct``).
        straddle_sigma: the straddle's sigma content (default ~0.8) used to turn
            the break ratio into an approximate standard-deviation move.

    Returns a dict:
        ``em_pct, gap_pct, break_ratio, sigma, direction, broke, label``
    where ``break_ratio = |gap_pct| / em_pct`` (1.0 == landed exactly on the edge
    of the implied range), ``sigma ≈ break_ratio * straddle_sigma``, ``direction``
    is ``"up"``/``"down"``/``"flat"``, and ``broke`` is ``break_ratio > 1``.
    """
    if not np.isfinite(em_pct) or em_pct <= 0:
        raise ValueError(f"em_break: em_pct must be > 0, got {em_pct!r}")
    if not np.isfinite(gap_pct):
        raise ValueError(f"em_break: gap_pct must be finite, got {gap_pct!r}")

    break_ratio = abs(gap_pct) / em_pct
    sigma = break_ratio * float(straddle_sigma)
    if gap_pct > 0:
        direction = "up"
    elif gap_pct < 0:
        direction = "down"
    else:
        direction = "flat"
    return {
        "em_pct": float(em_pct),
        "gap_pct": float(gap_pct),
        "break_ratio": float(break_ratio),
        "sigma": float(sigma),
        "direction": direction,
        "broke": bool(break_ratio > 1.0),
        "label": _label(break_ratio),
    }


def over_realization(
    cum_returns: Sequence[float],
    em_pct: float,
    gap_pct: float,
    *,
    stall_frac: float = 0.85,
) -> dict:
    """Is the post-earnings move still extending beyond the EM, or retracing?

    ``cum_returns`` is the sequence of cumulative signed returns from the
    pre-earnings close on each post-earnings session (session 1 ≈ ``gap_pct``).
    Everything is measured in the direction of the initial gap so a "persisting"
    move reads positive regardless of up/down.

    Returns a dict:
        ``peak_extension, latest_extension, gap_extension, persisting,
        retraced_frac, extended_beyond_gap``
    where ``*_extension`` are in units of the expected move (``/ em_pct``),
    ``persisting`` is latest >= ``stall_frac`` * peak (still near the highs of the
    move), and ``extended_beyond_gap`` is peak beyond the initial gap (the
    over-realization McGraw describes).
    """
    if not np.isfinite(em_pct) or em_pct <= 0:
        raise ValueError(f"over_realization: em_pct must be > 0, got {em_pct!r}")
    arr = np.asarray([c for c in cum_returns if np.isfinite(c)], dtype=float)
    if arr.size == 0:
        return {
            "peak_extension": 0.0,
            "latest_extension": 0.0,
            "gap_extension": abs(gap_pct) / em_pct,
            "persisting": False,
            "retraced_frac": 0.0,
            "extended_beyond_gap": False,
        }
    sign = -1.0 if gap_pct < 0 else 1.0
    directional = arr * sign  # positive = in the direction of the gap
    peak = float(np.max(directional))
    latest = float(directional[-1])
    peak_ext = peak / em_pct
    latest_ext = latest / em_pct
    gap_ext = abs(gap_pct) / em_pct
    retraced = 0.0 if peak <= 0 else max(0.0, (peak - latest) / peak)
    return {
        "peak_extension": float(peak_ext),
        "latest_extension": float(latest_ext),
        "gap_extension": float(gap_ext),
        "persisting": bool(peak > 0 and latest >= stall_frac * peak),
        "retraced_frac": float(retraced),
        "extended_beyond_gap": bool(peak_ext > gap_ext + 1e-9),
    }
