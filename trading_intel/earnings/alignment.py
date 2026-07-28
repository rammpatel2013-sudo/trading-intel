"""Signal-alignment scoring for the earnings-week screen (pure, no I/O).

Fahad-of-Jaguar's process, ported: among a week's earnings reporters, the
highest-quality asymmetric setups are where an independent research ANGLE
(fundamental bias) and the options FLOW point the SAME way into the print — and
an upward EPS-estimate REVISION is the top-quality confirmation. This module is
the pure ranking core: it takes the three per-name signals and returns an
alignment verdict + conviction tier. Descriptive ranking only — never a trade
signal (FlashAlpha rule 4).
"""

from __future__ import annotations

from dataclasses import dataclass


def _dir(x: float | None, dead: float = 0.0) -> int:
    """Sign of a signal past a dead-band: +1 / -1 / 0 (unknown or neutral)."""
    if x is None:
        return 0
    if x > dead:
        return 1
    if x < -dead:
        return -1
    return 0


@dataclass(frozen=True)
class AlignmentInputs:
    """Per-name signals feeding the screen.

    angle: research/watchlist sentiment in [-1, 1] (the 'hidden angle' direction).
    flow: net option premium / dollar-delta (>0 = net bullish flow).
    revision: EPS-estimate revision fraction (>0 = estimates revised UP).
    confidence: angle confidence in [0, 1] (optional; weights magnitude).
    """

    angle: float | None
    flow: float | None
    revision: float | None
    confidence: float | None = None


@dataclass(frozen=True)
class AlignmentResult:
    angle_dir: int
    flow_dir: int
    rev_dir: int
    aligned: bool
    tier: str
    tier_rank: int  # 1 highest .. 5 lowest (sort ascending)
    score: float
    bias: str


def score_alignment(inp: AlignmentInputs) -> AlignmentResult:
    """Rank a name on angle/flow/revision alignment (Fahad's design note)."""
    ad, fd, rd = _dir(inp.angle), _dir(inp.flow), _dir(inp.revision)
    aligned = ad != 0 and ad == fd
    conf = inp.confidence if inp.confidence is not None else 0.6

    # Tiering: the more independent signals that agree in one direction, the higher.
    if aligned and rd == ad:
        tier, rank = "1 · high (angle+flow+EPS↑)", 1
    elif aligned:
        tier, rank = "2 · aligned (angle+flow)", 2
    elif ad != 0 and rd == ad and fd in (0, ad):
        tier, rank = "3 · angle+EPS", 3
    elif ad != 0 or fd != 0:
        tier, rank = "4 · watch (single signal)", 4
    else:
        tier, rank = "— (no signal)", 5

    # Numeric conviction for ranking within a tier: signal magnitude + agreement bonuses.
    mag = abs(inp.angle or 0.0) * conf + min(abs(inp.flow or 0.0) / 1.0e7, 1.0)
    score = mag + (0.6 if aligned else 0.0) + (0.5 if (rd == ad and ad != 0) else 0.0)

    if ad > 0 or (ad == 0 and fd > 0):
        bias = "bullish"
    elif ad < 0 or (ad == 0 and fd < 0):
        bias = "bearish"
    else:
        bias = "mixed"

    return AlignmentResult(ad, fd, rd, aligned, tier, rank, round(score, 3), bias)
