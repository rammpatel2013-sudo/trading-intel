"""Knowledge-grounded per-tab summaries for the EOD vol report.

For each report tab, retrieve the most relevant desk-methodology chunks from the
pgvector knowledge base and ask the local LLM (Ollama, per CLAUDE.md rule 7) to
write a short analyst note grounded in those notes plus the tab's current
figures. Descriptive only (rule 4); degrades to an empty string if retrieval or
the LLM is unavailable, so the deterministic report always renders.

This module owns the LLM/knowledge orchestration so the report generator
(``scripts/eod_vol_report.py``) stays a thin layout layer.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from trading_intel.config import Settings
from trading_intel.memory.retrieval import format_kb, retrieve_chunks
from trading_intel.synthesis.prompts import EOD_KNOWLEDGE_PROMPT

if TYPE_CHECKING:  # keep the Ollama-backed import out of runtime/test-collect
    from sqlalchemy.orm import Session

    from trading_intel.synthesis.llm import LLMProvider

log = structlog.get_logger(__name__)

# tab id -> (display title, knowledge-base retrieval query)
TAB_KNOWLEDGE: dict[str, tuple[str, str]] = {
    "summary": (
        "Summary",
        "VIX regime ATM IV skew what the volatility surface is signaling "
        "reading not a diagnosis fear gauge",
    ),
    "decomp": (
        "Decomposition",
        "VIX decomposition sticky strike parallel shift put gradient downside "
        "convexity mechanical vs true fear",
    ),
    "term": (
        "Term Structure",
        "VIX term structure contango backwardation 9 day 3 month index curve "
        "futures curve forward expectations",
    ),
    "vvix": (
        "VVIX / VIX",
        "VVIX vol of vol VVIX VIX ratio Nations VolDex SkewDex TailDex "
        "interpretation latent fragility",
    ),
    "rabbit": (
        "Rabbit Hole",
        "VIX expiration OPEX gamma roll reversion clock tail hedges event "
        "horizon pinning",
    ),
    "cor": (
        "COR1M Map",
        "implied correlation COR1M dispersion VIXEQ DSPX single stock vol "
        "spread positioning event",
    ),
}

DEFAULT_K = 5
DEFAULT_KB_MAX_CHARS = 2200
DEFAULT_MAX_TOKENS = 430


def knowledge_summary(
    session: "Session",
    llm: "LLMProvider",
    settings: Settings,
    *,
    tab: str,
    as_of: str,
    data: str,
    k: int = DEFAULT_K,
    model: str | None = None,
) -> tuple[str, dict]:
    """Return ``(summary_text, metadata)`` for one tab.

    Retrieves desk-methodology chunks for the tab, then asks the LLM for a short
    grounded note. Returns empty text (and ``used_llm=False``) on any failure so
    callers can simply skip the knowledge block.
    """
    title, query = TAB_KNOWLEDGE.get(tab, (tab, ""))
    used_model = model or settings.LLM_DAILY_MODEL

    hits = []
    try:
        hits = retrieve_chunks(session, llm, query, kind="methodology", k=k)
    except Exception as exc:  # degrade: render without a knowledge block
        log.warning("eod_knowledge.retrieval_failed", tab=tab, error=str(exc))

    kb = format_kb(hits, max_chars=DEFAULT_KB_MAX_CHARS)

    text = ""
    try:
        prompt = EOD_KNOWLEDGE_PROMPT.format(
            tab=title,
            as_of=as_of,
            data=data,
            kb=kb or "(no reference notes found)",
        )
        text = llm.complete(prompt, model=used_model, max_tokens=DEFAULT_MAX_TOKENS).strip()
    except Exception as exc:
        log.warning("eod_knowledge.llm_failed", tab=tab, error=str(exc))
        text = ""

    meta = {
        "tab": tab,
        "used_llm": bool(text),
        "model": used_model if text else None,
        "sources": [h.title for h in hits],
        "n_hits": len(hits),
    }
    return text, meta


def build_knowledge_blocks(
    session: "Session",
    llm: "LLMProvider",
    settings: Settings,
    *,
    as_of: str,
    figures: dict[str, str],
) -> dict[str, str]:
    """Build the per-tab "Knowledge read" HTML blocks.

    ``figures`` maps a tab id to its current-figures text. Returns a map of tab
    id -> HTML ``.read`` block, omitting any tab whose summary could not be
    generated (so the deterministic report is unaffected for those tabs).
    """
    blocks: dict[str, str] = {}
    for tab, data in figures.items():
        text, meta = knowledge_summary(
            session, llm, settings, tab=tab, as_of=as_of, data=data
        )
        if not text:
            continue
        src = ""
        if meta["sources"]:
            uniq = ", ".join(dict.fromkeys(meta["sources"]))
            src = (
                '<p style="color:#5d6675;font-size:11px;margin-top:4px">'
                f"Knowledge base: {uniq}</p>"
            )
        blocks[tab] = (
            '<div class="read"><h3>Knowledge read — grounded in desk notes</h3>'
            f"<p>{text}</p>{src}</div>"
        )
    return blocks
