"""Extract a dynamic watchlist from research text via the LLM.

Provider-agnostic (takes an ``LLMProvider``): asks the model for the tickers a
research document discusses plus a one-line rationale, sentiment and themes for
each. Parsed defensively — small local models wrap JSON in prose or emit noise,
so we extract the first ``{...}`` block and clamp out-of-range values rather than
trusting the model.

Descriptive context only — populating a watchlist with rationale is NOT a trade
signal (FlashAlpha rule 4).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import structlog

from trading_intel.synthesis.llm import LLMProvider
from trading_intel.synthesis.prompts import WATCHLIST_EXTRACTION_PROMPT

log = structlog.get_logger(__name__)

MAX_CHARS = 14_000
_JSON_OBJ = re.compile(r"\{.*\}", re.DOTALL)
_TICKER_RE = re.compile(r"^[A-Z][A-Z.\-]{0,9}$")


@dataclass
class WatchlistCandidate:
    """One ticker surfaced from a research doc, with its LLM rationale."""

    symbol: str
    rationale: str = ""
    sentiment: float | None = None
    confidence: float | None = None
    themes: list[str] = field(default_factory=list)


def _coerce_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _clamp(value: float | None, lo: float, hi: float) -> float | None:
    return None if value is None else max(lo, min(hi, value))


def parse_candidates(raw: str) -> list[WatchlistCandidate]:
    """Parse the extraction JSON into candidates, tolerating small-model noise."""
    match = _JSON_OBJ.search(raw or "")
    if not match:
        log.warning("watchlist_extract.no_json", sample=(raw or "")[:120])
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        log.warning("watchlist_extract.bad_json", error=str(exc), sample=match.group(0)[:120])
        return []
    if not isinstance(data, dict):
        return []

    seen: set[str] = set()
    out: list[WatchlistCandidate] = []
    for item in data.get("tickers", []) or []:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol", "")).strip().upper()
        if not _TICKER_RE.match(symbol) or symbol in seen:
            continue
        seen.add(symbol)
        themes = [
            str(t).strip()[:64] for t in (item.get("themes", []) or []) if str(t).strip()
        ]
        out.append(
            WatchlistCandidate(
                symbol=symbol,
                rationale=str(item.get("rationale", "")).strip()[:500],
                sentiment=_clamp(_coerce_float(item.get("sentiment")), -1.0, 1.0),
                confidence=_clamp(_coerce_float(item.get("confidence")), 0.0, 1.0),
                themes=themes[:3],
            )
        )
    return out


def extract_watchlist(
    llm: LLMProvider, title: str, text: str, *, model: str | None = None
) -> list[WatchlistCandidate]:
    """Run the watchlist-extraction prompt over (a bounded slice of) ``text``."""
    prompt = WATCHLIST_EXTRACTION_PROMPT.format(title=title, text=text[:MAX_CHARS])
    raw = llm.complete(prompt, model=model, max_tokens=768)
    return parse_candidates(raw)
