"""Front-expiry gamma burn-off tracker — the structure-transition read.

The second half of McGraw's mechanic (``docs/learning/em-break-gamma-burnoff-digest.md``):
the convex, hedge-*forcing* gamma sits in the FRONT expiry; while it is live the
dealer hedge is mechanical and self-reinforcing (the flush). Once spot moves far
enough through the strikes, in-the-money options go delta-like and the move goes
convex -> LINEAR; at OPEX the front book expires and the amplifier is gone.

Two pure reads:

    front_dte_share()   from the per-expiration GEX term structure (``gex_term``),
                        what fraction of total |GEX| sits in the front expiry (and
                        the near bucket) — and how that share is decaying.
    phase()             mechanical / transition / linear, from the spot-ladder
                        dollar-gamma curve (``greeks/gamma_profile.py``): the ratio
                        of gamma AT spot to PEAK gamma. When spot has left the
                        strike cluster (ratio small) the convex phase is over.

``burnoff_state`` assembles both plus an OPEX countdown and a ``burned_off`` flag.

Pure transform, no I/O (the DB reads live at the tool/job edge). Descriptive
regime read only (FlashAlpha rule 4).
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

#: gamma_at_spot / peak_gamma band edges -> phase.
_MECHANICAL = 0.60  # spot still inside the dense strike cluster: convex hedging
_LINEAR = 0.25  # spot well outside: positions delta-like, hedge changes little

#: DTE at/under which an expiration counts as "near" (front-week + monthly reach).
NEAR_DTE: int = 7


def front_dte_share(
    term: Sequence[tuple[int | float, float]],
    *,
    near_dte: int = NEAR_DTE,
) -> dict:
    """Front-expiration and near-bucket share of total |GEX| from the term structure.

    Args:
        term: ``(dte, gex)`` pairs, one per expiration (signed ``gex`` fine — the
            share is over absolute magnitudes). Order irrelevant.
        near_dte: expirations with ``dte <= near_dte`` count toward ``near_share``.

    Returns ``front_dte, front_abs, total_abs, front_share, near_share,
    n_expirations``. Shares are 0 when there is no gamma. ``front_dte`` is the
    smallest non-negative DTE present.
    """
    pairs = [
        (float(d), abs(float(g)))
        for d, g in term
        if d is not None and g is not None and np.isfinite(d) and np.isfinite(g) and float(d) >= 0
    ]
    if not pairs:
        return {
            "front_dte": None,
            "front_abs": 0.0,
            "total_abs": 0.0,
            "front_share": 0.0,
            "near_share": 0.0,
            "n_expirations": 0,
        }
    total_abs = sum(g for _, g in pairs)
    front_dte = min(d for d, _ in pairs)
    front_abs = sum(g for d, g in pairs if d == front_dte)
    near_abs = sum(g for d, g in pairs if d <= near_dte)
    denom = total_abs if total_abs > 0 else 1.0
    return {
        "front_dte": float(front_dte),
        "front_abs": float(front_abs),
        "total_abs": float(total_abs),
        "front_share": float(front_abs / denom),
        "near_share": float(near_abs / denom),
        "n_expirations": len(pairs),
    }


def phase(
    gamma_at_spot: float | None,
    peak_gamma: float | None,
    *,
    front_share: float | None = None,
) -> str:
    """Classify the hedging phase: ``mechanical`` / ``transition`` / ``linear``.

    Primary read is ``|gamma_at_spot| / |peak_gamma|`` off the spot-ladder curve:
    near 1 means spot sits on the gamma peak (convex, mechanical hedging); small
    means spot has left the cluster (delta-like, linear). When the ladder is
    unavailable, degrade to a coarse proxy on ``front_share`` (a fat front book
    still concentrated reads mechanical). Returns ``"unknown"`` if neither input
    is usable.
    """
    if gamma_at_spot is not None and peak_gamma is not None:
        pk = abs(float(peak_gamma))
        if pk > 0 and np.isfinite(pk):
            ratio = abs(float(gamma_at_spot)) / pk
            if ratio >= _MECHANICAL:
                return "mechanical"
            if ratio >= _LINEAR:
                return "transition"
            return "linear"
    if front_share is not None and np.isfinite(front_share):
        if front_share >= 0.50:
            return "mechanical"
        if front_share >= 0.25:
            return "transition"
        return "linear"
    return "unknown"


def burnoff_state(
    term: Sequence[tuple[int | float, float]],
    *,
    dte_to_front_opex: int | float | None,
    gamma_at_spot: float | None = None,
    peak_gamma: float | None = None,
    prev_front_share: float | None = None,
    near_dte: int = NEAR_DTE,
) -> dict:
    """Assemble the burn-off read: front share + decay + phase + OPEX countdown.

    ``dte_to_front_opex`` is sessions/days until the front monthly expiry (<= 0
    means it has passed -> ``burned_off`` True). ``prev_front_share`` (yesterday's
    ``front_share``) yields ``share_decay`` (negative = the front book is bleeding
    out, the burn-off signature). ``gamma_at_spot``/``peak_gamma`` drive ``phase``.
    """
    share = front_dte_share(term, near_dte=near_dte)
    ph = phase(gamma_at_spot, peak_gamma, front_share=share["front_share"])
    decay = None
    if prev_front_share is not None and np.isfinite(prev_front_share):
        decay = float(share["front_share"] - prev_front_share)
    dfo = None if dte_to_front_opex is None else float(dte_to_front_opex)
    burned_off = bool(dfo is not None and dfo <= 0)
    # Also treat a near-zero front share as effectively burned off.
    if share["n_expirations"] and share["front_share"] < 0.05:
        burned_off = True
    return {
        **share,
        "phase": ph,
        "share_decay": decay,
        "dte_to_front_opex": dfo,
        "burned_off": burned_off,
    }
