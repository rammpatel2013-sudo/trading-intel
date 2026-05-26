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

# Refinitiv RIC / Yahoo exchange suffixes that break a plain US price lookup
# (e.g. ``RY.TO`` Toronto, ``AAPL.N`` NYSE). Share classes (``BRK.A``/``BRK.B``)
# are deliberately NOT in this set, so they are preserved.
_EXCHANGE_SUFFIXES = frozenset({
    "N", "O", "OQ", "K", "P", "Z", "PK", "TO", "V", "CN", "NE", "L", "PA", "DE",
    "AS", "BR", "MC", "MI", "SW", "ST", "HE", "OL", "CO", "VI", "AX", "NZ", "HK",
    "T", "SS", "SZ", "KS", "KQ", "TW", "TWO", "BO", "NS", "SI", "JK", "BK", "SA",
    "MX", "BA", "F",
})


def normalize_symbol(symbol: str) -> str:
    """Strip a trailing exchange suffix: ``RY.TO`` -> ``RY``, ``AAPL.N`` -> ``AAPL``.

    Exchange-coded tickers don't resolve on the US price feed, so we reduce them
    to the base ticker. Share classes (``BRK.A`` / ``BRK.B``) are preserved
    because ``A`` / ``B`` are not exchange codes.
    """
    sym = symbol.strip().upper()
    base, dot, suffix = sym.rpartition(".")
    if dot and base and suffix in _EXCHANGE_SUFFIXES:
        return base
    return sym


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
        symbol = normalize_symbol(str(item.get("symbol", "")))
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


def _chunks(text: str, *, size: int = MAX_CHARS, overlap: int = 1000) -> list[str]:
    """Split ``text`` into overlapping ~``size``-char chunks (small models have a
    tiny context, so one slice misses tickers in later pages of a big report)."""
    if not text:
        return [""]
    if len(text) <= size:
        return [text]
    step = max(1, size - overlap)
    return [text[i : i + size] for i in range(0, len(text), step)]


def extract_watchlist(
    llm: LLMProvider, title: str, text: str, *, model: str | None = None
) -> list[WatchlistCandidate]:
    """Extract tickers across the WHOLE document by chunking, then union (dedup).

    A big multi-ticker report exceeds a small local model's context, so we run the
    extraction over each chunk and merge the candidates (first mention wins).
    """
    seen: dict[str, WatchlistCandidate] = {}
    for chunk in _chunks(text):
        raw = llm.complete(
            WATCHLIST_EXTRACTION_PROMPT.format(title=title, text=chunk),
            model=model,
            max_tokens=768,
        )
        for cand in parse_candidates(raw):
            if cand.symbol not in seen:
                seen[cand.symbol] = cand
    return list(seen.values())
