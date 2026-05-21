"""Run research documents through the LLM: framework extraction + theme tagging.

Provider-agnostic — takes an ``LLMProvider`` (Ollama today). The knowledge
pipeline (``memory/pdf_pipeline.py``) calls these, so all model I/O for
ingestion sits behind one seam and the prompts stay in ``prompts.py``.

Tagging output is parsed defensively: small local models occasionally wrap JSON
in prose or emit minor noise, so we extract the first ``{...}`` block and clamp
out-of-range values rather than trusting the model to be well-behaved.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import structlog

from trading_intel.synthesis.llm import LLMProvider
from trading_intel.synthesis.prompts import (
    FRAMEWORK_EXTRACTION_PROMPT,
    THEME_TAGGING_PROMPT,
)

log = structlog.get_logger(__name__)

# Local models have small context windows; feed a bounded slice of each doc.
MAX_CHARS = 14_000
_VALID_SCOPES = {"macro", "sector", "company"}
_JSON_OBJ = re.compile(r"\{.*\}", re.DOTALL)


@dataclass
class ThemeTag:
    name: str
    scope: str
    sentiment: float | None = None
    confidence: float | None = None


@dataclass
class DocTags:
    summary: str = ""
    themes: list[ThemeTag] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)


def _clip(text: str) -> str:
    return text[:MAX_CHARS]


def _coerce_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def extract_framework(llm: LLMProvider, title: str, text: str, *, model: str | None = None) -> str:
    """Return markdown study-notes capturing the document's frameworks."""
    prompt = FRAMEWORK_EXTRACTION_PROMPT.format(title=title, text=_clip(text))
    return llm.complete(prompt, model=model, max_tokens=2048).strip()


def _parse_tags(raw: str) -> DocTags:
    """Parse the tagging JSON, tolerating small-model noise."""
    match = _JSON_OBJ.search(raw or "")
    if not match:
        log.warning("tagging.no_json", sample=(raw or "")[:120])
        return DocTags()
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        log.warning("tagging.bad_json", error=str(exc), sample=match.group(0)[:120])
        return DocTags()
    if not isinstance(data, dict):
        return DocTags()

    themes: list[ThemeTag] = []
    for raw_theme in data.get("themes", []) or []:
        if not isinstance(raw_theme, dict):
            continue
        name = str(raw_theme.get("name", "")).strip()
        if not name:
            continue
        scope = str(raw_theme.get("scope", "")).strip().lower()
        if scope not in _VALID_SCOPES:
            scope = "macro"
        sentiment = _coerce_float(raw_theme.get("sentiment"))
        if sentiment is not None:
            sentiment = max(-1.0, min(1.0, sentiment))
        confidence = _coerce_float(raw_theme.get("confidence"))
        if confidence is not None:
            confidence = max(0.0, min(1.0, confidence))
        themes.append(
            ThemeTag(name=name[:128], scope=scope, sentiment=sentiment, confidence=confidence)
        )

    symbols = [str(s).strip().upper() for s in (data.get("symbols", []) or []) if str(s).strip()]
    return DocTags(
        summary=str(data.get("summary", "")).strip(),
        themes=themes,
        symbols=symbols,
    )


def tag_document(llm: LLMProvider, title: str, text: str, *, model: str | None = None) -> DocTags:
    """Tag a document into themes + a short summary."""
    prompt = THEME_TAGGING_PROMPT.format(title=title, text=_clip(text))
    raw = llm.complete(prompt, model=model, max_tokens=512)
    return _parse_tags(raw)
