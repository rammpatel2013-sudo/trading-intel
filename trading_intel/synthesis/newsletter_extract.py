"""Extract structured LEVELS + conditional SCENARIOS from a markets newsletter (local LLM).

Doc / VolSignals / Kurt-Altrichter give concrete levels (0DTE gamma flip, call/put
walls, expected range, gamma levels); Norseman gives the Bull/Bear Line + session
count + A-D read; and — the part Mithil wants — they narrate IF-THEN scenarios
("if SPX holds above X → grind to Y; lose X → air to Z"). This does ONE local-Ollama
pass over the stored letter body and pulls both into structured records, so the
synthesis engine can (a) cross-check the author's stated levels against OUR computed
flip/walls and (b) surface which if-then branch is live vs current price.

Local model only (rule 7 — routed through the ``LLMProvider`` Protocol, default
Ollama). Descriptive research input (rule 4): it transcribes what the author STATED;
it does not judge or signal. Degrades to an empty record — never raises. Mirrors
``earnings/kpi_extract.py``.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import structlog

from trading_intel.synthesis.llm import LLMProvider

log = structlog.get_logger(__name__)

_UNITS = {"SPX", "SPY", "QQQ", "points", "percent", "vols", "vix"}
_DIRECTIONS = {"bullish", "bearish", "neutral"}
_CONFIDENCE = {"high", "medium", "low"}
_MAX_ITEMS = 12

_PROMPT = """You are reading a markets / options newsletter and extracting ONLY the
concrete numbers and IF-THEN scenarios the author EXPLICITLY states. Return ONLY a
JSON object (no prose, no code fence):

{{
  "levels": [
    {{"name": "<short snake_case label: gamma_flip, call_wall, put_wall, bull_bear_line, expected_move, key_support, key_resistance, target>",
      "value": <number only, no $ or commas>,
      "unit": "<SPX|SPY|QQQ|points|percent|vols|vix or null>",
      "note": "<= 10 word context or null>"}}
  ],
  "scenarios": [
    {{"trigger": "<the condition, e.g. 'SPX holds above 6350' or 'if VIX term flips to backwardation'>",
      "consequence": "<what the author says happens then>",
      "direction": "<bullish|bearish|neutral or null>",
      "confidence": "<high|medium|low or null>"}}
  ],
  "one_line": "<= 20 word summary of the author's main call"
}}

Only include what the author states. Numbers exactly as written. Use empty lists
when there are none. Do NOT invent levels or scenarios.

NEWSLETTER (source: {source}; may be truncated):
{body}
"""


@dataclass(frozen=True, slots=True)
class NewsletterRead:
    """Structured read of one newsletter body (all best-effort)."""

    source: str
    levels: list[dict] = field(default_factory=list)  # {name, value, unit, note}
    scenarios: list[dict] = field(default_factory=list)  # {trigger, consequence, direction, confidence}
    one_line: str | None = None

    @property
    def empty(self) -> bool:
        return not self.levels and not self.scenarios


def _coerce_json(raw: str) -> dict:
    """Tolerant parse of the model reply into a dict (handles code fences / prose)."""
    if not raw:
        return {}
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return {}
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        return {}


def _num(v: object) -> float | None:
    if v is None:
        return None
    try:
        return float(str(v).replace("$", "").replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _clean_str(v: object, limit: int) -> str | None:
    return str(v).strip()[:limit] if isinstance(v, str) and str(v).strip() else None


def _parse_levels(raw: object) -> list[dict]:
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    for item in raw[:_MAX_ITEMS]:
        if not isinstance(item, dict):
            continue
        name = _clean_str(item.get("name"), 40)
        val = _num(item.get("value"))
        if not name or val is None:
            continue
        unit = item.get("unit")
        unit = str(unit) if unit in _UNITS else None
        out.append({"name": name.lower().replace(" ", "_"), "value": val, "unit": unit,
                    "note": _clean_str(item.get("note"), 120)})
    return out


def _parse_scenarios(raw: object) -> list[dict]:
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    for item in raw[:_MAX_ITEMS]:
        if not isinstance(item, dict):
            continue
        trigger = _clean_str(item.get("trigger"), 200)
        if not trigger:
            continue
        direction = item.get("direction")
        direction = str(direction) if direction in _DIRECTIONS else None
        conf = item.get("confidence")
        conf = str(conf) if conf in _CONFIDENCE else None
        out.append({"trigger": trigger, "consequence": _clean_str(item.get("consequence"), 240),
                    "direction": direction, "confidence": conf})
    return out


def extract_newsletter(
    source: str,
    body: str,
    llm: LLMProvider,
    *,
    model: str | None = None,
    max_chars: int = 16_000,
) -> NewsletterRead:
    """Pull levels + if-then scenarios from one newsletter body. Never raises."""
    if not body or not body.strip():
        return NewsletterRead(source=source)

    prompt = _PROMPT.format(source=source, body=body[:max_chars])
    try:
        reply = llm.complete(prompt, model=model, max_tokens=1024)
    except Exception as exc:  # noqa: BLE001 — model down/timeout: degrade, don't crash the job
        log.warning("newsletter_extract.llm_failed", source=source, error=str(exc))
        return NewsletterRead(source=source)

    obj = _coerce_json(reply)
    return NewsletterRead(
        source=source,
        levels=_parse_levels(obj.get("levels")),
        scenarios=_parse_scenarios(obj.get("scenarios")),
        one_line=_clean_str(obj.get("one_line"), 200),
    )
