"""Extract earnings KPIs from a call transcript (local LLM).

The swing dossier's scorecard (§4) needs the SaaS/operating KPIs that set the
post-print reaction — DBNRR / net-retention, cRPO & RPO growth, big-customer
counts, gross margin, revenue growth, guidance direction. None of these live in
a financial statement (they're in the prepared remarks / release), so we do ONE
local-Ollama pass over the transcript to pull them into a structured record,
banked per quarter so "decelerating retention" becomes a curve, not a data point.

Local model only (rule 7 — routed through the ``LLMProvider`` Protocol, default
Ollama). Deterministic, descriptive research input (rule 4): it transcribes what
management stated; it does not judge or signal. Every field degrades to ``None``
when the model can't find it. Pairs with ``earnings/inflection.py`` (that reads
tone *change*; this reads the *numbers*).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict

import structlog

from trading_intel.synthesis.llm import LLMProvider

log = structlog.get_logger(__name__)

# Keep the ask tight and JSON-only; a small tagging model handles this well.
_PROMPT = """You are extracting reported metrics from an earnings call transcript.
Return ONLY a JSON object (no prose) with these keys; use null when the company
did not state the figure. Numbers only (no % sign, no $), as the company reported them.

{{
  "dbnrr_pct": <dollar-based net retention / net revenue retention, percent, e.g. 118>,
  "revenue_growth_yoy_pct": <total revenue YoY growth percent>,
  "gross_margin_pct": <gross margin percent>,
  "operating_margin_pct": <operating/non-GAAP operating margin percent>,
  "crpo_growth_yoy_pct": <current RPO YoY growth percent>,
  "rpo_growth_yoy_pct": <total RPO YoY growth percent>,
  "customers_over_100k": <count of customers > $100k ARR>,
  "customers_over_1m": <count of >$1M customers, or null>,
  "fcf_margin_pct": <free-cash-flow margin percent, or null>,
  "guidance_direction": <"raised" | "maintained" | "lowered" | null>,
  "one_line_kpi_read": <<=20 word summary of the KPI trajectory>
}}

TRANSCRIPT (may be truncated):
{transcript}
"""

# per-field sanity bounds — reject a hallucinated out-of-range number.
_BOUNDS: dict[str, tuple[float, float]] = {
    "dbnrr_pct": (50, 250),
    "revenue_growth_yoy_pct": (-100, 500),
    "gross_margin_pct": (0, 100),
    "operating_margin_pct": (-100, 100),
    "crpo_growth_yoy_pct": (-100, 500),
    "rpo_growth_yoy_pct": (-100, 500),
    "fcf_margin_pct": (-100, 100),
    "customers_over_100k": (0, 5_000_000),
    "customers_over_1m": (0, 1_000_000),
}


@dataclass(frozen=True, slots=True)
class EarningsKPIs:
    """Structured KPI record for one call (all optional)."""

    symbol: str
    dbnrr_pct: float | None = None
    revenue_growth_yoy_pct: float | None = None
    gross_margin_pct: float | None = None
    operating_margin_pct: float | None = None
    crpo_growth_yoy_pct: float | None = None
    rpo_growth_yoy_pct: float | None = None
    customers_over_100k: float | None = None
    customers_over_1m: float | None = None
    fcf_margin_pct: float | None = None
    guidance_direction: str | None = None
    one_line_kpi_read: str | None = None

    def as_row(self) -> dict:
        return asdict(self)


def _coerce_json(raw: str) -> dict:
    """Best-effort parse of the model's reply into a dict (tolerant of code fences)."""
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


def _num(v: object, field: str) -> float | None:
    if v is None:
        return None
    try:
        x = float(str(v).replace("%", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    lo, hi = _BOUNDS.get(field, (float("-inf"), float("inf")))
    return x if lo <= x <= hi else None


def extract_kpis(
    symbol: str,
    transcript: str,
    llm: LLMProvider,
    *,
    model: str | None = None,
    max_chars: int = 24_000,
) -> EarningsKPIs:
    """Pull the KPI record for one call. ``model`` defaults to the tagging model.

    The transcript is truncated to ``max_chars`` (the metrics are in prepared
    remarks up front). Returns an all-``None`` record when the transcript is empty
    or the model reply can't be parsed — never raises.
    """
    if not transcript or not transcript.strip():
        return EarningsKPIs(symbol=symbol.upper())

    prompt = _PROMPT.format(transcript=transcript[:max_chars])
    try:
        reply = llm.complete(prompt, model=model, max_tokens=512)
    except Exception as exc:  # model down / timeout — degrade, don't crash the job
        log.warning("kpi_extract.llm_failed", symbol=symbol, error=str(exc))
        return EarningsKPIs(symbol=symbol.upper())

    obj = _coerce_json(reply)
    guidance = obj.get("guidance_direction")
    if guidance not in ("raised", "maintained", "lowered"):
        guidance = None
    read = obj.get("one_line_kpi_read")
    read = str(read)[:200] if isinstance(read, str) else None

    return EarningsKPIs(
        symbol=symbol.upper(),
        dbnrr_pct=_num(obj.get("dbnrr_pct"), "dbnrr_pct"),
        revenue_growth_yoy_pct=_num(obj.get("revenue_growth_yoy_pct"), "revenue_growth_yoy_pct"),
        gross_margin_pct=_num(obj.get("gross_margin_pct"), "gross_margin_pct"),
        operating_margin_pct=_num(obj.get("operating_margin_pct"), "operating_margin_pct"),
        crpo_growth_yoy_pct=_num(obj.get("crpo_growth_yoy_pct"), "crpo_growth_yoy_pct"),
        rpo_growth_yoy_pct=_num(obj.get("rpo_growth_yoy_pct"), "rpo_growth_yoy_pct"),
        customers_over_100k=_num(obj.get("customers_over_100k"), "customers_over_100k"),
        customers_over_1m=_num(obj.get("customers_over_1m"), "customers_over_1m"),
        fcf_margin_pct=_num(obj.get("fcf_margin_pct"), "fcf_margin_pct"),
        guidance_direction=guidance,
        one_line_kpi_read=read,
    )
