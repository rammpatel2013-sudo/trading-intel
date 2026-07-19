"""Earnings-call analysis: transcript access + inflection detection.

Transcripts are free on the CVForge FMP passthrough (no new vendor). ``inflection``
scores the positive/negative *inflection* (the quarter-over-quarter change in
tone + guidance) transparently; a local-Ollama quote-extraction layer is Slice 2.
Descriptive only (FlashAlpha rule 4).
"""

from __future__ import annotations

from trading_intel.earnings.inflection import (
    InflectionRead,
    ToneScore,
    guidance_signal,
    read_inflection,
    score_tone,
)

__all__ = [
    "InflectionRead",
    "ToneScore",
    "guidance_signal",
    "read_inflection",
    "score_tone",
]
