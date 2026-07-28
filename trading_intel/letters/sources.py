"""Investor-letter / filing source registry (from Investor_Letters_Tracker.xlsx).

Config only — the ``letters_fetch`` / ``filings_fetch`` jobs read these. Two lanes:
  - ``substack``  : an RSS feed to poll; new posts are saved as letters.
  - ``edgar_13f`` : a fund CIK whose quarterly 13F holdings we diff.
Website-scrape sources are a later increment (add per-site parsers then).

Reference data only. Extend from the tracker's ``SEC CIK`` column; only funds that
actually file 13F belong in the EDGAR lane (Peter Kamin / Sudbury / Hoak file
13D/Form 4, not 13F — handle those separately).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SourceKind = Literal["substack", "edgar_13f"]


@dataclass(frozen=True, slots=True)
class LetterSource:
    """One ingestion source. ``ref`` is an RSS feed URL (substack) or a SEC CIK."""

    fund: str
    kind: SourceKind
    ref: str
    cadence: str = "quarterly"


#: Substack RSS lane (increment 1). Feed convention is ``<base>/feed``.
SUBSTACK_SOURCES: tuple[LetterSource, ...] = (
    LetterSource("Alluvial Capital", "substack", "https://alluvial.substack.com/feed"),
    LetterSource(
        "Eagle Point Capital",
        "substack",
        "https://eaglepointcapital.substack.com/feed",
        "semiannual",
    ),
    LetterSource("Voss Capital", "substack", "https://vosscapital.substack.com/feed"),
    LetterSource("Icaria Capital", "substack", "https://icariacap.substack.com/feed", "periodic"),
    LetterSource(
        "Greystone Capital", "substack", "https://poundtherockinvesting.substack.com/feed"
    ),
    LetterSource("Hinde Group", "substack", "https://hindegroup.substack.com/feed"),
)

#: EDGAR 13F lane (increment 2). ``ref`` = CIK (zero-padded to 10 digits at fetch).
EDGAR_13F_SOURCES: tuple[LetterSource, ...] = (
    LetterSource("Nantahala Capital", "edgar_13f", "1472322"),
    LetterSource("Makaira Partners", "edgar_13f", "1540866"),
    LetterSource("TowerView LLC", "edgar_13f", "1166573"),
    LetterSource("Mill Road Capital", "edgar_13f", "1512275"),
    LetterSource("Minerva Advisors", "edgar_13f", "1541536"),
    LetterSource("Atlantic Investment Mgmt", "edgar_13f", "1063296"),
    LetterSource("Solas Capital", "edgar_13f", "1604867"),
    LetterSource("GoldenTree Asset Mgmt", "edgar_13f", "1278951"),
    LetterSource("Cannell Capital", "edgar_13f", "1058854"),
    LetterSource("Engaged Capital", "edgar_13f", "1559771"),
    LetterSource("Voss Capital", "edgar_13f", "1730145"),
    LetterSource("Vulcan Value Partners", "edgar_13f", "1556785"),
    LetterSource("SVN Capital", "edgar_13f", "1807555"),
    LetterSource("Roubaix Capital", "edgar_13f", "1769700"),
    LetterSource("Kerrisdale Capital", "edgar_13f", "1569688"),
    LetterSource("Fairholme Capital", "edgar_13f", "1056831"),
)


#: Gmail sender allowlist — the PRIMARY letters lane (docs/investor_letters_pipeline.md).
#: Compiled from an inbox survey 2026-07-19; extend as new senders appear.
GMAIL_SENDERS: tuple[str, ...] = (
    # direct fund letters (often PDF attachments)
    "tim@meditationcapital.com",
    "investorrelations@gatorcapital.com",
    "info@deepsailcapital.com",
    "hardasset2023@gmail.com",
    # research services (rich, attachments)
    "info@specialsitsresearch.com",
    "specialsitsresearch@gmail.com",
    "alerts@jaguaranalytics.com",
    # aggregators (compile many funds)
    "hfbestideas@substack.com",
    "info@buysidedigest.com",
    "johnny@acquirersmultiple.com",
    # value letters / essays
    "onveston@substack.com",
    "vdl@substack.com",
    "phenomcapital@substack.com",
    "eliantcap@substack.com",
    "vk@imausa1.com",
    "bestanchorstocks@substack.com",
    "uncoveralpha@substack.com",
    # vol / options
    "docmcgraw@substack.com",
    "jaredhstocks@substack.com",
    "longandshortmkts@substack.com",
)


def gmail_senders() -> tuple[str, ...]:
    """The Gmail sender allowlist, de-duplicated."""
    return tuple(dict.fromkeys(s.strip().lower() for s in GMAIL_SENDERS if s.strip()))


def substack_sources() -> tuple[LetterSource, ...]:
    """Substack RSS sources, de-duplicated by feed URL."""
    seen: set[str] = set()
    out: list[LetterSource] = []
    for s in SUBSTACK_SOURCES:
        if s.ref not in seen:
            seen.add(s.ref)
            out.append(s)
    return tuple(out)


def edgar_13f_sources() -> tuple[LetterSource, ...]:
    """EDGAR 13F sources, de-duplicated by CIK."""
    seen: set[str] = set()
    out: list[LetterSource] = []
    for s in EDGAR_13F_SOURCES:
        cik = s.ref.strip()
        if cik not in seen:
            seen.add(cik)
            out.append(LetterSource(s.fund.strip(), s.kind, cik, s.cadence))
    return tuple(out)
