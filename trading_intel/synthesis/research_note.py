"""Per-ticker narrative research note from free sources.

Assembles the uploaded research-PDF text, the latest SEC 10-K, FMP profile /
financials / news, and the ticker's live options-vol regime into one prompt and
writes a narrative via the LLM (Ollama), with a deterministic fallback when the
LLM is unavailable. Pure formatting helpers + a single generator. Descriptive
research read-through only - FlashAlpha rule 4, never a trade signal.
"""
from __future__ import annotations

import structlog

from trading_intel.synthesis.llm import LLMProvider
from trading_intel.synthesis.prompts import RESEARCH_NOTE_PROMPT

log = structlog.get_logger(__name__)


def _truncate(text: str | None, n: int) -> str:
    return (text or "")[:n]


def format_fundamentals(
    profile: dict | None, financials: list[dict] | None, news: list[dict] | None
) -> str:
    """Plain-text fundamentals block from FMP profile / financials / news."""
    lines: list[str] = []
    if profile:
        lines.append(
            f"{profile.get('companyName', '?')} — {profile.get('sector', '?')}/"
            f"{profile.get('industry', '?')}, mktCap {profile.get('mktCap', '?')}."
        )
        if profile.get("description"):
            lines.append(_truncate(profile["description"], 600))
    for fs in (financials or [])[:2]:
        lines.append(
            f"FY{fs.get('calendarYear', '?')}: revenue {fs.get('revenue')}, "
            f"net income {fs.get('netIncome')}, gross margin {fs.get('grossProfitRatio')}."
        )
    if news:
        lines.append("Recent news:")
        for n in news[:6]:
            lines.append(f"- {str(n.get('publishedDate', ''))[:10]} {n.get('title', '')} "
                         f"({n.get('site', '')})")
    return "\n".join(lines) if lines else "(no fundamentals / news available)"


def build_research_note(
    ticker: str,
    *,
    llm: LLMProvider | None = None,
    pdf_text: str = "",
    tenk_text: str = "",
    profile: dict | None = None,
    financials: list[dict] | None = None,
    news: list[dict] | None = None,
    regime_md: str = "",
    model: str | None = None,
    max_pdf: int = 4000,
    max_tenk: int = 4000,
) -> str:
    """Generate the markdown research note for ``ticker`` (LLM, deterministic fallback)."""
    fundamentals = format_fundamentals(profile, financials, news)
    prompt = RESEARCH_NOTE_PROMPT.format(
        ticker=ticker,
        pdf=_truncate(pdf_text, max_pdf) or "(none)",
        tenk=_truncate(tenk_text, max_tenk) or "(none)",
        fundamentals=fundamentals,
        regime=regime_md or "(none)",
    )
    if llm is not None:
        try:
            return llm.complete(prompt, model=model, max_tokens=1000).strip()
        except Exception as exc:  # Ollama down / model missing - deterministic fallback
            log.warning("research_note.llm_failed", ticker=ticker, error=str(exc))
    return (
        f"## {ticker} — research note (deterministic)\n\n"
        f"### Fundamentals & news\n{fundamentals}\n\n"
        f"### Options / vol regime\n{regime_md or '(none)'}\n\n"
        f"### Uploaded research excerpt\n{_truncate(pdf_text, 800) or '(none)'}"
    )
