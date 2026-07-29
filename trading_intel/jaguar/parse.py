"""Deterministic parse of a Jaguar email body (JaguarLive / First Read / Trade Alert).

Jaguar's emails follow a consistent shape: a name callout reads
``Company Name (TICK) - <commentary>``, option prints spell out
``12,000 December 50 calls at $4.48``, and the day is segmented by ``<N> hours ago``
timestamps and section headers (Weekend Research, Conversations, Top Economic News).
Parsing that structure deterministically — rather than trusting an LLM to lift tickers
and sizes — means the numbers in the brief can't be hallucinated; the LLM layer
(:mod:`jaguar.extract`) only condenses the prose grounded here. Pure + unit-tested on
real bodies. Descriptive research context only (FlashAlpha rule 4).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# "<N> hours/minutes ago" lines segment the day into blocks.
_TS = re.compile(r"^\s*\d+\s+(?:hours?|minutes?|days?)\s+ago\s*$", re.M | re.I)
# Name callout header at a block's start: "Company Name (TICK) - rest...".
_CALLOUT = re.compile(r"^(?P<name>[^\n(]{1,64}?)\s*\((?P<tk>[A-Z]{1,5})\)\s*[-–—:]\s*(?P<rest>.*)$")
# Option-flow print: "12,000 December 50 calls at $4.48", "1,000+ ... call spread".
# The tail keeps decimals ("$4.48") but stops at a real sentence break (". ").
_CONTRACT = re.compile(
    r"[\d,]+\+?\s+(?:contracts?\s+(?:of\s+)?)?"
    r"(?:[A-Z][a-z]+\s+)?(?:\(\d+\)\s+)?(?:Weekly\s+)?[\d/.\s-]*?"
    r"(?:strike\s+)?(?:call|put)s?(?:[^.\n]|\.\d)*",
    re.I,
)
# A magnitude-suffixed dollar figure (skips bare strikes like $4.48).
_PREMIUM = re.compile(r"\$[\d,.]+\s*(?:million|billion|M\b|K\b|bn\b)", re.I)
_PREM_CTX = ("premium", "bet", "bullish", "bearish")
_PDF = re.compile(r"https?://[^\s)>\"']+?/wp-content/uploads/[^\s)>\"']+?\.pdf", re.I)
_EARNINGS = re.compile(r"\b(reports?|earnings|before the open|after the close)\b", re.I)
# Words that show up in (PARENS) but are not tickers.
_STOP = {
    "CONTEXT",
    "HERE",
    "URL",
    "PDF",
    "CEO",
    "CFO",
    "AI",
    "US",
    "USA",
    "EU",
    "UK",
    "GMV",
    "FLNG",
    "DUV",
    "IPO",
    "GDP",
    "CPI",
    "FED",
    "FOMC",
    "ETF",
    "ETFS",
    "M&A",
    "YTD",
    "YOY",
    "QOQ",
    "EPS",
    "PT",
    "OI",
    "IV",
    "II",
    "III",
}


@dataclass(frozen=True, slots=True)
class Callout:
    """One name Fahad flagged — ticker, name, his prose, and any grounded flow."""

    ticker: str
    name: str
    text: str
    contracts: list[str] = field(default_factory=list)
    premium: str | None = None
    links: list[str] = field(default_factory=list)
    earnings: bool = False


def blocks(body: str) -> list[str]:
    """Split a body into content blocks on the ``<N> hours ago`` timestamp lines."""
    return [p.strip() for p in _TS.split(body) if p.strip()]


def _clean(text: str) -> str:
    """Drop boilerplate ("Img", bare URLs) and collapse whitespace for prose use."""
    keep = [
        ln
        for ln in text.splitlines()
        if ln.strip() and ln.strip() != "Img" and not ln.strip().startswith("http")
    ]
    return re.sub(r"[ \t]+", " ", "\n".join(keep)).strip()


def _premium(text: str) -> str | None:
    """The trade premium: prefer a $ figure sitting next to premium/bet/bullish words,
    else the first non-'billion' magnitude figure (skips revenue/market-cap billions)."""
    for m in _PREMIUM.finditer(text):
        window = text[max(0, m.start() - 30) : m.end() + 30].lower()
        if any(w in window for w in _PREM_CTX):
            return m.group(0)
    for m in _PREMIUM.finditer(text):
        if "billion" not in m.group(0).lower() and "bn" not in m.group(0).lower():
            return m.group(0)
    return None


def parse_callouts(body: str, *, max_callouts: int = 40) -> list[Callout]:
    """Every ``Name (TICK) - ...`` callout in the body, with grounded flow prints.

    Deduped by ticker (the richest mention wins). Order preserved as they appear.
    """
    found: dict[str, Callout] = {}
    for blk in blocks(body):
        first = blk.split("\n", 1)[0]
        m = _CALLOUT.match(first)
        if not m:
            continue
        tk = m.group("tk").upper()
        if tk in _STOP:
            continue
        contracts = [re.sub(r"\s+", " ", c.group(0)).strip(" .") for c in _CONTRACT.finditer(blk)]
        cand = Callout(
            ticker=tk,
            name=m.group("name").strip(),
            text=_clean(blk),
            contracts=contracts[:4],
            premium=_premium(blk),
            links=list(dict.fromkeys(_PDF.findall(blk))),
            earnings=bool(_EARNINGS.search(blk)),
        )
        prev = found.get(tk)
        if prev is None or len(cand.text) > len(prev.text):
            found[tk] = cand
    return list(found.values())[:max_callouts]


def find_block(body: str, prefix: str) -> str | None:
    """The first block whose text starts with ``prefix`` (e.g. 'Weekend Research')."""
    low = prefix.lower()
    for blk in blocks(body):
        if blk.lower().startswith(low):
            return _clean(blk)
    return None


def first_read_highlights(body: str) -> list[str]:
    """The ``* ...`` bullet highlights from a First-Read block (incl. Notable Callout)."""
    return [
        re.sub(r"\s+", " ", ln.strip()[1:]).strip()
        for ln in body.splitlines()
        if ln.strip().startswith("*")
    ]


def pdf_links(body: str) -> list[str]:
    """All public wp-content PDF links in the body (gated mp-files are never matched)."""
    return list(dict.fromkeys(_PDF.findall(body)))
