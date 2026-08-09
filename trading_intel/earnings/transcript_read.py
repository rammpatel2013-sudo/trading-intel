"""Rich transcript read for the swing dossier — Q&A extraction + multi-engine tone.

Three tone engines, layered so the automated NAS job never breaks:

  1. Loughran-McDonald lexicon (``pysentiment2``, full LM word lists) — the primary,
     deterministic polarity on the *prepared remarks* and the *Q&A* separately
     (the Q&A carries more signal). Falls back to the curated Stage-1 lexicon in
     ``earnings.inflection`` if pysentiment2 is not installed.
  2. QoQ inflection (``earnings.inflection.read_inflection``) — tone *change* vs the
     prior call + guidance raise/cut cue phrases: the actual inflection.
  3. FinBERT (``yiyanghkust/finbert-tone``) — BEST-EFFORT second opinion. Loads only
     where ``transformers``+``torch``+the model are present (a laptop-run dossier);
     on the NAS (3.8 GB RAM) the import fails and this degrades to ``None`` — the
     lexicon path is unaffected.

The Q&A pairs (which analyst asked what, and how management answered) come from the
local Ollama pass (rule 7). Everything here is descriptive (rule 4); the module is
rule-1 isolated (no vendor client; the optional heavy deps are lazy-imported).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from trading_intel.earnings.inflection import (
    UNCERTAINTY,
    InflectionRead,
    read_inflection,
    score_tone,
)

# ── prepared / Q&A segmentation ─────────────────────────────────────────────
# Earliest phrase that opens the Q&A on a call. Matched case-insensitively; the
# first hit *after* the opening 15% of the text splits prepared remarks from Q&A.
_QA_MARKERS: tuple[str, ...] = (
    "question-and-answer session",
    "question and answer session",
    "questions and answers",
    "q&a session",
    "we will now begin the question",
    "we will now open the line",
    "we'll now open the line",
    "we will now open it up for questions",
    "open it up for questions",
    "floor is now open for questions",
    "we will now take questions",
    "we'll now take questions",
    "first question comes from",
    "first question is from",
    "our first question",
    "[operator instructions]",
    "(operator instructions)",
)

_WORD = re.compile(r"[a-z']+")
_SENT = re.compile(r"(?<=[.!?])\s+")


def split_prepared_qa(text: str) -> tuple[str, str]:
    """``(prepared, qa)`` — the Q&A starts at the earliest marker past 15% of the text."""
    if not text:
        return "", ""
    low = text.lower()
    floor = int(len(text) * 0.15)
    hits = [i for i in (low.find(m, floor) for m in _QA_MARKERS) if i != -1]
    if not hits:
        return text, ""
    cut = min(hits)
    return text[:cut], text[cut:]


# ── engine 1: Loughran-McDonald lexicon (pysentiment2, full lists) ──────────
_LM = None  # cached pysentiment2.LM instance, or False if unavailable


def _lm():
    global _LM
    if _LM is None:
        try:
            import pysentiment2 as ps

            _LM = ps.LM()
        except Exception:  # noqa: BLE001 — optional dep; degrade to the curated lexicon
            _LM = False
    return _LM or None


def _count(text: str, lex) -> int:
    return sum(1 for w in _WORD.findall(text.lower()) if w in lex)


@dataclass(frozen=True, slots=True)
class LexTone:
    positive: int
    negative: int
    uncertain: int
    words: int
    engine: str  # "loughran-mcdonald" | "stage-1"

    @property
    def polarity(self) -> float:
        pol = self.positive + self.negative
        return (self.positive - self.negative) / pol if pol else 0.0

    @property
    def uncertainty_density(self) -> float:
        return self.uncertain / self.words if self.words else 0.0


def lexicon_tone(text: str) -> LexTone:
    """Polarity + uncertainty over ``text`` — full LM if pysentiment2 is present."""
    if not text:
        return LexTone(0, 0, 0, 0, "stage-1")
    lm = _lm()
    unc = _count(text, UNCERTAINTY)  # LM uncertainty list ~ our Stage-1 set
    if lm is not None:
        try:
            toks = lm.tokenize(text)
            sc = lm.get_score(toks)
            return LexTone(int(sc["Positive"]), int(sc["Negative"]), unc, len(toks) or 1,
                           "loughran-mcdonald")
        except Exception:  # noqa: BLE001
            pass
    ts = score_tone(text)
    return LexTone(ts.positive, ts.negative, ts.uncertain, ts.total_words or 1, "stage-1")


# ── engine 3: FinBERT (best-effort) ─────────────────────────────────────────
_FINBERT = None  # cached HF pipeline, or False if unavailable


def _finbert():
    global _FINBERT
    if _FINBERT is None:
        try:
            from transformers import pipeline  # heavy; absent on the NAS

            _FINBERT = pipeline("text-classification", model="yiyanghkust/finbert-tone",
                                top_k=None)
        except Exception:  # noqa: BLE001 — no transformers/torch/model → skip cleanly
            _FINBERT = False
    return _FINBERT or None


def finbert_tone(text: str, *, cap: int = 40) -> dict | None:
    """Sentence-level pos/neu/neg shares over the first ``cap`` sentences, or ``None``."""
    pipe = _finbert()
    if pipe is None or not text:
        return None
    sents = [s.strip() for s in _SENT.split(text) if len(s.strip()) > 25][:cap]
    if not sents:
        return None
    try:
        out = pipe([s[:280] for s in sents])
        agg = {"Positive": 0, "Neutral": 0, "Negative": 0}
        for row in out:
            best = max(row, key=lambda r: r["score"])
            agg[best["label"].capitalize()] = agg.get(best["label"].capitalize(), 0) + 1
        n = sum(agg.values()) or 1
        return {"positive": agg["Positive"] / n, "neutral": agg["Neutral"] / n,
                "negative": agg["Negative"] / n, "n": n}
    except Exception:  # noqa: BLE001
        return None


# ── Q&A pair extraction (local Ollama) ──────────────────────────────────────
_STANCES = ("direct", "confident", "hedged", "cautious", "evasive")


def _parse_json_array(raw: str) -> list[dict]:
    if not raw:
        return []
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1] if s.count("```") >= 2 else s.strip("`")
        s = s.lstrip("json").strip()
    a, b = s.find("["), s.rfind("]")
    if a == -1 or b == -1 or b < a:
        return []
    try:
        data = json.loads(s[a:b + 1])
    except Exception:  # noqa: BLE001
        return []
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


def extract_qa(qa_text: str, llm, *, model=None, n: int = 6) -> list[dict]:
    """The ``n`` most material analyst exchanges via local Ollama; [] if unavailable."""
    if llm is None or not qa_text.strip():
        return []
    prompt = (
        f"You are reading the Q&A portion of an earnings call. Extract the {n} MOST "
        "MATERIAL analyst exchanges (skip pleasantries). For EACH exchange return a JSON "
        "object with EXACTLY these keys:\n"
        '  "analyst": analyst name and/or firm if stated, else ""\n'
        '  "topic": the subject in <=5 words\n'
        '  "question": a <=22-word paraphrase of what was asked\n'
        '  "answer": a <=32-word paraphrase of how management answered\n'
        '  "stance": one of direct | confident | hedged | cautious | evasive '
        "(how forthcoming the answer was)\n"
        "Return ONLY a JSON array of these objects, newest/most-important first, no prose.\n\n"
        + qa_text[:16000]
    )
    try:
        raw = llm.complete(prompt, model=model, max_tokens=1100)
    except Exception:  # noqa: BLE001
        return []
    rows = _parse_json_array(raw)
    out: list[dict] = []
    for r in rows[:n]:
        st = str(r.get("stance", "")).lower().strip()
        out.append({
            "analyst": str(r.get("analyst", ""))[:60],
            "topic": str(r.get("topic", ""))[:48],
            "question": str(r.get("question", ""))[:180],
            "answer": str(r.get("answer", ""))[:240],
            "stance": st if st in _STANCES else "",
        })
    return out


# ── top-level assembly ──────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class TranscriptRead:
    symbol: str
    inflection: InflectionRead
    tone_prepared: LexTone
    tone_qa: LexTone | None
    finbert: dict | None
    qa: list[dict] = field(default_factory=list)


def analyze(symbol: str, this_text: str, prior_text: str | None, llm, *,
            model=None, n_qa: int = 6, want_finbert: bool = True) -> TranscriptRead:
    """One rich read: prepared/Q&A tone, QoQ inflection, best-effort FinBERT, Q&A pairs."""
    prepared, qa = split_prepared_qa(this_text)
    infl = read_inflection(symbol, this_text, prior_text)
    tone_prepared = lexicon_tone(prepared or this_text)
    tone_qa = lexicon_tone(qa) if qa else None
    fb = finbert_tone(qa or this_text) if want_finbert else None
    qa_rows = extract_qa(qa or this_text, llm, model=model, n=n_qa)
    return TranscriptRead(symbol=symbol, inflection=infl, tone_prepared=tone_prepared,
                          tone_qa=tone_qa, finbert=fb, qa=qa_rows)
