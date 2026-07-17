"""Earnings-call inflection features (transparent, Stage-1).

Detects a positive / negative *inflection* in an earnings call by measuring the
CHANGE vs the prior quarter — tone delta, uncertainty delta — plus explicit
guidance raise/cut cues. Pure and lexicon-based so it is transparent and
unit-tested without a vendor or an LLM (the Ollama quote-extraction is Slice 2).

The lexicons are a curated Stage-1 finance cue set (extendable to the full
Loughran-McDonald lists later), matching the "transparent weighted composite now,
fitted model later" pattern used elsewhere. Descriptive only (FlashAlpha rule 4):
this ranks/labels candidate inflections; it is not a signal or advice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ── Curated Stage-1 finance lexicons (single tokens, lowercase) ─────────
POSITIVE: frozenset[str] = frozenset(
    """accelerate accelerated accelerating acceleration ahead beat beats best
    breakout confidence confident demand efficiency efficient exceed exceeded
    exceeding expansion favorable gain gains grew growth higher improve improved
    improvement increase increased increasing momentum opportunity optimistic
    outperform outperformed positive profitability profitable raise raised raising
    record records robust strength strong stronger strongest success successful
    tailwind tailwinds upside win winning wins""".split()
)
NEGATIVE: frozenset[str] = frozenset(
    """below cautious challenge challenges challenging contraction cut cuts cutting
    decelerate deceleration decline declined declining decrease decreased delay
    delayed difficult disappointing disappointment downturn headwind headwinds
    impairment layoffs loss losses lower lowered lowering macro miss missed
    negative pressure pressured restructuring shortfall slow slowdown slower
    sluggish soft softer softness weak weaker weakness worse worsening""".split()
)
UNCERTAINTY: frozenset[str] = frozenset(
    """approximately assume assumption cautious contingent could depend depends may
    maybe might possible possibly potential potentially risk risks uncertain
    uncertainty unclear unpredictable volatile volatility""".split()
)

# Guidance cue phrases (substring match on lowercased text).
GUIDANCE_UP: tuple[str, ...] = (
    "raising our guidance",
    "raising guidance",
    "raised our guidance",
    "increasing our outlook",
    "raising our outlook",
    "above the high end",
    "better than expected",
    "ahead of our expectations",
    "ahead of plan",
    "exceeded our expectations",
    "raising our full-year",
    "increasing our full-year",
)
GUIDANCE_DOWN: tuple[str, ...] = (
    "lowering our guidance",
    "lowering guidance",
    "reducing our guidance",
    "cutting our guidance",
    "below the low end",
    "weaker than expected",
    "worse than expected",
    "below our expectations",
    "reducing our outlook",
    "lowering our outlook",
    "softer than expected",
    "reducing our full-year",
)

# Label thresholds on the [-1, 1] inflection score.
POS_THRESHOLD = 0.15
NEG_THRESHOLD = -0.15

_WORD = re.compile(r"[a-z']+")


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


@dataclass(frozen=True, slots=True)
class ToneScore:
    """Lexicon tone for one transcript."""

    positive: int
    negative: int
    uncertain: int
    total_words: int

    @property
    def tone(self) -> float:
        """(pos - neg) / (pos + neg) in [-1, 1]; 0 when no polarity words hit."""
        pol = self.positive + self.negative
        return (self.positive - self.negative) / pol if pol else 0.0

    @property
    def uncertainty_density(self) -> float:
        return self.uncertain / self.total_words if self.total_words else 0.0


@dataclass(frozen=True, slots=True)
class InflectionRead:
    """A positive/negative/steady inflection read for one name."""

    symbol: str
    tone: float
    prior_tone: float | None
    tone_delta: float | None
    uncertainty_density: float
    uncertainty_delta: float | None
    guidance_signal: float  # [-1, 1]: +up cues, -down cues
    score: float  # [-1, 1] overall inflection
    label: str  # "positive inflection" | "negative inflection" | "steady / no clear inflection"


def score_tone(text: str) -> ToneScore:
    """Count positive / negative / uncertainty lexicon hits in ``text``."""
    tokens = _WORD.findall(text.lower())
    pos = neg = unc = 0
    for t in tokens:
        if t in POSITIVE:
            pos += 1
        if t in NEGATIVE:
            neg += 1
        if t in UNCERTAINTY:
            unc += 1
    return ToneScore(positive=pos, negative=neg, uncertain=unc, total_words=len(tokens))


def guidance_signal(text: str) -> float:
    """Net guidance-cue direction in [-1, 1] (+raise, -cut); 0 with no cues."""
    low = text.lower()
    up = sum(low.count(p) for p in GUIDANCE_UP)
    down = sum(low.count(p) for p in GUIDANCE_DOWN)
    total = up + down
    return (up - down) / total if total else 0.0


def read_inflection(symbol: str, this_text: str, prior_text: str | None = None) -> InflectionRead:
    """Inflection read for one call, using the QoQ tone change when a prior exists.

    Score = 0.6*tone_delta + 0.4*guidance - uncertainty penalty when a prior quarter
    is available (the inflection = the *change*); absolute tone + guidance otherwise.
    """
    this = score_tone(this_text)
    gsig = guidance_signal(this_text)

    prior_tone: float | None = None
    tone_delta: float | None = None
    unc_delta: float | None = None
    if prior_text is not None:
        prior = score_tone(prior_text)
        prior_tone = prior.tone
        tone_delta = this.tone - prior.tone
        unc_delta = this.uncertainty_density - prior.uncertainty_density

    if tone_delta is not None:
        base = 0.6 * tone_delta + 0.4 * gsig
        if unc_delta is not None:  # rising uncertainty tempers a positive read
            base -= _clamp(unc_delta * 5.0, 0.0, 0.2)
    else:
        base = 0.5 * this.tone + 0.5 * gsig

    score = round(_clamp(base), 3)
    if score >= POS_THRESHOLD:
        label = "positive inflection"
    elif score <= NEG_THRESHOLD:
        label = "negative inflection"
    else:
        label = "steady / no clear inflection"

    return InflectionRead(
        symbol=symbol,
        tone=round(this.tone, 3),
        prior_tone=round(prior_tone, 3) if prior_tone is not None else None,
        tone_delta=round(tone_delta, 3) if tone_delta is not None else None,
        uncertainty_density=round(this.uncertainty_density, 5),
        uncertainty_delta=round(unc_delta, 5) if unc_delta is not None else None,
        guidance_signal=round(gsig, 3),
        score=score,
        label=label,
    )
