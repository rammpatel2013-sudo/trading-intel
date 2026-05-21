"""All LLM prompts live in one place (per MASTER_PLAN.md §5.4).

Currently: research-document framework extraction + theme tagging, used by the
knowledge pipeline (``memory/pdf_pipeline.py``). Provider-agnostic — the rendered
text is fed to whatever ``LLMProvider`` is wired (Ollama today).
"""

from __future__ import annotations

FRAMEWORK_EXTRACTION_PROMPT = """\
You are a quantitative options-research analyst. The text below is extracted
from a research document titled "{title}". Produce concise, faithful study
notes that capture the analytical FRAMEWORKS it teaches — not a book report.

Rules:
- Use ONLY information present in the text. Do not invent results or numbers.
- Preserve formulas exactly as written (inline code or LaTeX-style text).
- If the text looks like only an excerpt or opening section, say so and cover
  what is present.

Output GitHub-flavoured markdown with exactly these sections:

## Overview
One short paragraph: what this document is about and why it matters for
options / volatility / dealer-positioning research.

## Key concepts
Bullet list of the core ideas, each with a one-line definition.

## Formulas & methods
The key formulas, models, or procedures, each with a one-line explanation.

## Connection to GEX / dealer positioning / vol surface
How these ideas relate to gamma/vanna/charm exposure, dealer hedging, the
volatility surface, or regime classification. If there is no clear connection,
say so plainly.

## Caveats & assumptions
Assumptions, limitations, or conditions the author flags.

Document text:
\"\"\"
{text}
\"\"\"
"""

THEME_TAGGING_PROMPT = """\
You are tagging a research document for a personal options-research knowledge
base. The document is titled "{title}".

Return ONLY a single JSON object (no prose, no markdown fences) shaped like:
{{
  "summary": "<= 80 word plain-English summary of the document",
  "themes": [
    {{"name": "short theme label", "scope": "macro|sector|company",
      "sentiment": <number between -1 and 1>, "confidence": <number 0 to 1>}}
  ],
  "symbols": ["TICKER", ...]
}}

Guidance:
- 1 to 5 themes. "scope" must be exactly one of: macro, sector, company.
- sentiment: the document's overall stance on that theme (-1 bearish .. 1
  bullish; 0 for neutral/technical material).
- symbols: tickers explicitly discussed; use [] if none.
- Output valid JSON only.

Document text:
\"\"\"
{text}
\"\"\"
"""
