"""Swing-setup feature + scoring layer (Stage-1).

Shared implementation for the on-demand swing report, the daily ``swing_features``
collector, and the P4 ``strategies/swing_options.py`` generator. Descriptive /
candidate only — not signals until validated (FlashAlpha rule 4).
"""

from __future__ import annotations

from trading_intel.swing.features import iv_rv_ratio, realized_vol, skew_25d
from trading_intel.swing.scoring import SwingScore, score_setup

__all__ = [
    "SwingScore",
    "iv_rv_ratio",
    "realized_vol",
    "score_setup",
    "skew_25d",
]
