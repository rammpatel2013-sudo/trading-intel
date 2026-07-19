"""Track B — market-wide credit-income ranking (premium selling).

Where the swing generator (Track A) hunts high-PoP *debit* setups on cheap vol,
Track B ranks a BROAD universe for defined-risk *credit* structures — selling
premium where vol is rich. Pure ranking core; the live CVForge breadth pull + HTML
live in ``scripts/credit_income_scan.py``.

Richness is scored two ways so it works before per-name IV-rank history exists
(MEMORY: percentiles bank forward, cross-sectional rank interim):
  * absolute IV/RV (ATM IV ÷ 20d realized), and
  * cross-sectional IV/RV rank within the scanned batch (``rank_universe``).

Side follows the shared lean (``trading_intel.swing.score_setup``): bullish -> bull
put credit spread, bearish -> bear call credit spread, neutral -> iron condor.

Descriptive / candidate only (FlashAlpha rule 4) — a ranked idea list, never an
alert or advice.
"""

from __future__ import annotations

import bisect
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from trading_intel.swing.scoring import score_setup

# Absolute IV/RV richness -> base points (selling premium wants IV >= RV).
_RICH_ABS = ((1.30, 50.0), (1.15, 35.0), (1.00, 20.0))
W_XS_RANK = 30.0  # cross-sectional IV/RV-rank weight
W_SKEW = 20.0  # skew-harvest alignment weight

Number = float | int


@dataclass(frozen=True, slots=True)
class CreditIdea:
    """A ranked credit-income candidate (descriptive)."""

    symbol: str
    score: float  # 0-100 richness suitability (higher = richer premium to sell)
    side: str  # "put" | "call" | "iron_condor"
    structure: str
    lean: str
    iv_rv: float | None
    iv_rv_rank: float | None  # cross-sectional 0..1 within the scanned batch
    atm_iv: float | None


def _abs_richness_points(iv_rv: float | None) -> float:
    if iv_rv is None:
        return 0.0
    for threshold, pts in _RICH_ABS:
        if iv_rv >= threshold:
            return pts
    return 5.0  # cheap vol — poor to sell


def _skew_points(side: str, skew_25d: float | None) -> float:
    """Reward skew that fattens the sold wing (put skew for a bull put spread)."""
    if not isinstance(skew_25d, Number):
        return 0.0
    if side == "put":  # bull put spread sells puts -> richer put skew helps
        return W_SKEW if skew_25d > 0 else 0.0
    if side == "call":  # bear call spread sells calls -> call-rich (skew < 0) helps
        return W_SKEW if skew_25d < 0 else 0.0
    return W_SKEW * 0.5  # iron condor benefits from either wing being fat


def _side_for(lean: str) -> tuple[str, str]:
    if lean == "bullish":
        return "put", "Bull put credit spread (sell ~25-30d put, 30-45 DTE)"
    if lean == "bearish":
        return "call", "Bear call credit spread (sell ~25-30d call, 30-45 DTE)"
    return "iron_condor", "Iron condor (sell ~25d strangle, 30-45 DTE)"


def credit_income_score(
    feat: Mapping[str, object], *, iv_rv_rank: float | None = None
) -> CreditIdea:
    """Score one name's credit-income suitability + pick a defined-risk side.

    ``iv_rv_rank`` is the optional cross-sectional 0..1 rank of this name's IV/RV
    within the scanned batch (from :func:`rank_universe`).
    """
    lean = score_setup(feat).lean
    side, structure = _side_for(lean)

    iv_rv = feat.get("iv_rv")
    iv_rv_f = float(iv_rv) if isinstance(iv_rv, Number) else None

    pts = _abs_richness_points(iv_rv_f)
    if iv_rv_rank is not None:
        pts += W_XS_RANK * iv_rv_rank
    pts += _skew_points(side, feat.get("skew_25d"))
    score = round(min(100.0, pts), 1)

    return CreditIdea(
        symbol=str(feat.get("symbol", "")),
        score=score,
        side=side,
        structure=structure,
        lean=lean,
        iv_rv=iv_rv_f,
        iv_rv_rank=iv_rv_rank,
        atm_iv=float(feat["atm_iv"]) if isinstance(feat.get("atm_iv"), Number) else None,
    )


def rank_universe(feats: Sequence[Mapping[str, object]]) -> list[CreditIdea]:
    """Score every name with a cross-sectional IV/RV rank, ranked richest-first."""
    vals = sorted(float(f["iv_rv"]) for f in feats if isinstance(f.get("iv_rv"), Number))

    def xs_rank(v: object) -> float | None:
        if not vals or not isinstance(v, Number):
            return None
        return bisect.bisect_right(vals, float(v)) / len(vals)

    ideas = [credit_income_score(f, iv_rv_rank=xs_rank(f.get("iv_rv"))) for f in feats]
    ideas.sort(key=lambda i: i.score, reverse=True)
    return ideas
