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


SURFACE_INTERPRETATION_PROMPT = """\
You are a volatility analyst writing a short desk note on the current implied
volatility surface. You are given (a) structured metrics computed from the live
surface and (b) reference notes from the desk's own methodology library.

Write a concise interpretation (<= 220 words, plain English, GitHub markdown)
of what the surface is currently saying: the skew (downside vs upside demand),
the term structure (carry / stress), forward vol, and what regime it implies.
Ground your reasoning in the reference notes where relevant. Do NOT give trade
recommendations or price targets — this is a regime read-through only.

Metrics (JSON):
{metrics}

Reference notes (desk methodology):
{kb}
"""


SURFACE_FLOW_REPORT_PROMPT = """\
You are a volatility desk analyst writing a surface + option-flow read-through in
the house style. You are given (a) structured surface metrics, (b) an option-flow
summary, and (c) reference notes from the desk methodology library. INTERPRET the
data into a story — do NOT just restate the numbers.

Write a concise desk note (<= 400 words, GitHub markdown) in EXACTLY these three
sections:

## The Read
Tell the surface as a story: the crash-bid / deep-OTM put wing, the call wing, the
ATM term structure (contango vs backwardation), and the put-skew steepness (risk
reversal, front vs back). Quote the key numbers, but say what each one MEANS about
demand for downside protection vs upside.

## The Flow
Interpret today's option flow: the put/call notional tilt, and the largest
structures — and what each EXPRESSES (e.g. an ATM straddle = a clean
direction-agnostic vol bet; a far-OTM put wing = cheap tail insurance; deep-ITM
calls = synthetic long delta; a repeated equal-size slice = a sweep worked across
venues).

## Speculation vs Hedging
Classify the two books running on the same tape: speculation (selective,
structure-aware, cheap-IV near-the-money) vs hedging (broad, programmatic,
multi-tenor OTM puts paying the steeper front-month put IV). Use the notional tilt,
but judge the STRUCTURE — layered/multi-tenor/distributed reads as portfolio
management, not a directional bet. Conclude the regime.

Hard rules:
- Interpret, do not enumerate. Use ONLY the data provided; never invent figures.
- Descriptive regime read only — NO trade recommendations, entries, price targets,
  or direction calls ("buy/sell/long/short/expect/should").

Surface metrics (JSON):
{metrics}

Option flow:
{flow}

Reference notes (desk methodology):
{kb}
"""


WATCHLIST_EXTRACTION_PROMPT = """\
You are building a watchlist from a company / equity research document titled
"{title}". Identify the tradeable tickers the document actually discusses and,
for each, capture WHY it is interesting per the document.

Return ONLY a single JSON object (no prose, no markdown fences) shaped like:
{{
  "tickers": [
    {{"symbol": "TICKER",
      "rationale": "<= 30 word reason this name is on the watchlist, per the doc",
      "sentiment": <number between -1 and 1>,
      "confidence": <number 0 to 1>,
      "themes": ["short theme label", ...]}}
  ]
}}

Guidance:
- Use ONLY tickers explicitly discussed; do NOT invent symbols. Use the
  exchange ticker (e.g. AAPL), uppercase. Return "tickers": [] if none.
- rationale: grounded in the document's own argument, not generic.
- sentiment: the document's stance on the name (-1 bearish .. 1 bullish; 0
  neutral). confidence: how strongly the doc supports it (0..1).
- themes: 0-3 short labels (e.g. "AI capex", "margin expansion").
- Output valid JSON only.

Document text:
\"\"\"
{text}
\"\"\"
"""


AM_SUMMARY_PROMPT = """\
You are the desk analyst writing the morning regime note for {as_of}. You are
given a structured snapshot of the watchlist built from collected options and
price data (already formatted as markdown tables below). Write a concise
pre-market note in plain English (GitHub markdown, <= 320 words).

Structure the note in three parts, in this order:
1. **Market regime** — one short paragraph on SPX/SPY/QQQ: net GEX sign, dealer
   gamma regime (above/below flip), ATM IV, and the 0DTE cumulative
   gamma/vanna/charm read where present.
2. **Research watchlist** — for each research-surfaced ticker, one line on why
   it is on the list (use the supplied rationale/sentiment), tied to whatever
   regime metric it currently shows. If there are none, say so in one line.
3. **Watchlist regime** — call out only the few names with the most notable
   regime reads (largest weekly GEX change, gamma regime flips, skew extremes,
   one-sided flow tilt). Do not enumerate every symbol.

Hard rules:
- Describe the CURRENT regime only. This is a read-through, NOT a forecast.
- Do NOT give trade recommendations, price targets, entries, or predictions of
  direction. No "buy/sell/long/short", no "expect", no "should".
- Use ONLY the numbers in the data below; do not invent figures.
- The reference notes are desk methodology for FRAMING only — use them to choose
  which regime features matter and how to describe them. Never pull figures or
  ticker-specific claims from the notes; the data tables are the only source of
  numbers.

Reference notes (desk methodology — for framing only):
{kb}

Data (already computed — your single source of truth):
{data}
"""


EOD_KNOWLEDGE_PROMPT = """\
You are the desk volatility analyst writing the "{tab}" section of the end-of-day
vol report for {as_of}. You are given (a) today's figures for this section,
including day-over-day and week-over-week moves, and (b) reference notes pulled
from the desk knowledge base.

Write a single tight paragraph (5-7 sentences) that INTERPRETS the data — do not
merely restate the numbers. Cover, in order:
1. What the current reading says about the regime.
2. How it shifted versus yesterday and versus last week, and why that change matters.
3. The forward implication — what to watch over the next day / next week.
Ground every interpretive claim in the reference notes: lean on the framework
they describe (e.g. sticky-strike vs parallel-shift, index curve vs futures
curve, dispersion mechanics) rather than inventing your own.

Hard rules:
- Describe the CURRENT regime and what to watch — a read-through, NOT a forecast.
- No trade recommendations, price targets, or directional calls. No
  "buy/sell/long/short", no "expect prices to", no "should".
- Use ONLY the figures and reference notes provided; do not invent numbers.
- Plain English prose. No headings, no bullet lists.

Current figures for this section (with day-over-day and week-over-week moves):
{data}

Reference notes (desk knowledge base — your grounding):
{kb}
"""


RESEARCH_NOTE_PROMPT = """\
You are an equity research analyst writing a concise note on {ticker}. You are
given (a) an excerpt from an uploaded research report, (b) the latest 10-K text,
(c) company fundamentals + recent news, and (d) the live options/vol regime for
the name. Synthesize them into a narrative - do NOT just list the inputs.

Write <= 450 words, GitHub markdown, in these sections:

## Snapshot
What the company is (sector, size) and the one-line setup from the uploaded research.

## Fundamentals & filings
The revenue/earnings trend and margins, plus anything notable from the 10-K (risk
factors, MD&A themes). Ground every claim in the numbers/text provided.

## What's moving it
Recent news / catalysts.

## Options & vol regime
The current gamma regime, skew / term structure, and IV-HV (rich vs cheap) for the
name - what the options market is pricing.

## The read
Tie the uploaded research thesis together with the fundamentals and the vol regime.

Hard rules: use ONLY the data provided; never invent figures. Descriptive research
read-through - NO trade recommendations, price targets, or direction calls.

Uploaded research excerpt:
{pdf}

10-K excerpt:
{tenk}

Fundamentals + news:
{fundamentals}

Options / vol regime:
{regime}
"""
