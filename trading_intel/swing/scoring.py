"""Stage-1 swing conviction scorer (transparent weighted composite).

The single source of truth for the Stage-1 score, direction lean, and candidate
defined-risk structure. Extracted from ``scripts/swing_report.py`` so the report,
the ``swing_features`` collector, and the P4 ``strategies/swing_options.py``
generator all share one implementation (see the swing-system build, P3).

Feature keys are the canonical ``swing_features`` column names (``spot``,
``sma50``, ``px_vs_sma50``, ``rsi14``, ``dex``, ``gex``, ``iv_rv`` ...) so a banked
row scores without remapping. Pure — no I/O, numpy/stdlib only.

Stage-1 uses ABSOLUTE thresholds. It is DESCRIPTIVE only: the score ranks
candidate setups; it is neither a signal nor advice (FlashAlpha rule 4). The
validated generator + backtest are P4/P6, and percentile features (IV-rank, skew
percentile) refine this once the daily snapshots bank enough history (P2).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

# ── Stage-1 weights (points; max 100) ──────────────────────────────────
W_TREND = 20.0  # price vs SMA50
W_RSI = 20.0  # RSI14 momentum
W_DEX = 15.0  # net delta-exposure lean
W_IV_RV = 25.0  # IV/RV richness (cheap vol -> higher buy conviction)
W_GEX = 20.0  # positioning context present

# ── IV/RV richness cutoffs (ATM IV ÷ 20d realized) ─────────────────────
IV_RV_CHEAP = 1.10  # below -> richest points (long premium favored)
IV_RV_ELEVATED = 1.30  # above -> vol rich (prefer credit structures)
IV_RV_LONG_PREM = 1.15  # below -> tag "long premium favored"

# ── RSI lean thresholds ────────────────────────────────────────────────
RSI_BULL = 55.0
RSI_BEAR = 45.0

Number = float | int


@dataclass(frozen=True, slots=True)
class SwingScore:
    """Stage-1 conviction (0-100), directional lean, and a candidate structure."""

    score: float
    lean: str  # "bullish" | "bearish" | "neutral"
    structure: str


def _trend_up(feat: Mapping[str, object]) -> bool | None:
    """Price-above-SMA50 test from ``px_vs_sma50`` (preferred) or ``spot``/``sma50``."""
    pv = feat.get("px_vs_sma50")
    if isinstance(pv, Number):
        return pv > 0
    spot, sma = feat.get("spot"), feat.get("sma50")
    if isinstance(spot, Number) and isinstance(sma, Number) and sma:
        return spot > sma
    return None


def _rsi_points(rsi: float) -> float:
    """Triangular kernel peaking at 60 (bull) / 40 (bear); 0 at the extremes."""
    if rsi >= 50:
        return max(0.0, W_RSI - abs(rsi - 60) * 0.6)
    return max(0.0, W_RSI - abs(40 - rsi) * 0.6)


def score_setup(feat: Mapping[str, object]) -> SwingScore:
    """Transparent Stage-1 conviction, lean, and a defined-risk structure idea.

    ``feat`` is a mapping of canonical swing features (missing/None keys simply
    contribute nothing). Direction is a vote across trend, RSI, and DEX lean; the
    structure is chosen from the lean and IV/RV richness.
    """
    pts = 0.0
    direction = 0

    up = _trend_up(feat)
    if up is not None:
        direction += 1 if up else -1
        pts += W_TREND if up else 0.0

    rsi = feat.get("rsi14")
    if rsi is None:
        rsi = feat.get("rsi")  # tolerate the report's legacy key
    if isinstance(rsi, Number):
        if rsi >= RSI_BULL:
            direction += 1
        elif rsi <= RSI_BEAR:
            direction -= 1
        pts += _rsi_points(float(rsi))

    dex = feat.get("dex")
    if isinstance(dex, Number):
        direction += 1 if dex > 0 else -1
        pts += W_DEX

    ivrv = feat.get("iv_rv")
    ivrv_f = float(ivrv) if isinstance(ivrv, Number) else None
    if ivrv_f is not None:
        pts += W_IV_RV if ivrv_f < IV_RV_CHEAP else (10.0 if ivrv_f < IV_RV_ELEVATED else 5.0)

    if feat.get("gex") is not None:
        pts += W_GEX

    score = round(min(100.0, pts), 1)
    lean = "bullish" if direction > 0 else "bearish" if direction < 0 else "neutral"
    return SwingScore(score=score, lean=lean, structure=_structure(lean, ivrv_f))


def _structure(lean: str, ivrv: float | None) -> str:
    """Candidate defined-risk structure for a lean + IV/RV richness (descriptive)."""
    cheap = ivrv is not None and ivrv < IV_RV_LONG_PREM
    rich = ivrv is not None and ivrv > IV_RV_ELEVATED
    if lean == "bullish":
        structure = (
            "Bull put credit spread (harvest put skew)" if rich else "Call debit spread (45-90 DTE)"
        )
    elif lean == "bearish":
        structure = "Bear call credit spread" if rich else "Put debit spread (45-90 DTE)"
    else:
        structure = "Iron condor / stand aside" if rich else "No edge — wait"
    if cheap and lean != "neutral":
        structure += " * long premium favored (IV<RV-ish)"
    return structure
