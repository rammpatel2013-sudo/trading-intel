"""Scheduled job: fetch new Substack investor letters -> research watchlist + knowledge.

Polls each configured Substack feed, saves genuinely-new posts as markdown under the
letters dir, then runs the EXISTING research ingestion
(``memory.watchlist_ingest.ingest_folder``) so tickers land on the RESEARCH watchlist
(``watchlist_entries``) and the knowledge/chunk pipeline indexes them. Idempotent:
``save_entry`` skips existing files and the ingest dedups by SHA (rule 5). Local LLM
only (``LLM_TAGGING_MODEL``, rule 7); descriptive research context, never a signal
(rule 4).

Manual run:
    python -m trading_intel.scheduler.jobs.letters_fetch
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path
from xml.etree.ElementTree import ParseError

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_intel.config import Settings, get_settings
from trading_intel.letters import substack
from trading_intel.letters.sources import substack_sources
from trading_intel.memory.models import NewsletterLevel, NewsletterScenario, ResearchNote
from trading_intel.memory.watchlist_ingest import ingest_folder
from trading_intel.synthesis.llm import LLMProvider

log = structlog.get_logger(__name__)

#: Letters live under the research tree so the nightly research ingest also sees them.
DEFAULT_LETTERS_DIR = Path("research/company/letters")

#: Sender-slug fragment -> stable note key the daily brief reads for commentary.
#: (The .md bodies live in the ephemeral --rm container; only the DB survives, so
#: we persist the newest body per source as a ResearchNote.)
_SOURCE_KEYS = {
    "docmcgraw": "__DOC__",
    "jaguaranalytics": "__JAGUAR__",
    "longandshort": "__LONGSHORT__",
    "specialsits": "__SITS__",
    # added 2026-08-11 — the positioning/timing sources. Fragments match the
    # sender-slug folder names; if a source's letters land in a differently-named
    # folder its note just stays empty until the fragment is corrected.
    "volsignals": "__VOLSIGNALS__",
    "lumida": "__LUMIDA__",
    "kurt": "__KURT__",
    "norseman": "__NORSEMAN__",
}
_NOTE_MAX_CHARS = 8000

#: The subset of stored notes we run the local-Ollama levels/scenarios extractor on
#: (Doc/VolSignals/Kurt/Norseman give levels + if-then; the flow/prose sources don't).
_SIGNAL_SOURCES = {
    "__DOC__": "DOC",
    "__VOLSIGNALS__": "VOLSIGNALS",
    "__KURT__": "KURT",
    "__NORSEMAN__": "NORSEMAN",
}


def _store_source_notes(session: Session, root: Path) -> int:
    """Persist the newest letter body per source as a ResearchNote (rule 4)."""
    if not root.is_dir():
        return 0
    today = date.today()
    written = 0
    for frag, key in _SOURCE_KEYS.items():
        mds = [
            f
            for d in root.iterdir()
            if d.is_dir() and frag in d.name
            for f in d.glob("*.md")
        ]
        if not mds:
            continue
        newest = max(mds, key=lambda p: p.stat().st_mtime)
        try:
            raw = newest.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        # Drop the "# subject / From: / Date:" header block the saver prepends.
        parts = raw.split("\n\n", 2)
        body = (parts[-1] if len(parts) == 3 else raw)[:_NOTE_MAX_CHARS]
        if len(body) < 80:
            continue
        stmt = (
            pg_insert(ResearchNote)
            .values(
                symbol=key, as_of=today, note_md=body,
                sources=newest.name[:128], model="letters",
            )
            .on_conflict_do_update(
                index_elements=["symbol", "as_of"],
                set_={"note_md": body, "sources": newest.name[:128]},
            )
        )
        session.execute(stmt)
        written += 1
    session.commit()
    return written


def _extract_source_signals(
    session: Session, llm: LLMProvider, *, model: str | None = None
) -> int:
    """Local-Ollama pass over the stored letter bodies → newsletter_levels + scenarios.

    Reads the newest ResearchNote per signal source (DOC / VOLSIGNALS / KURT /
    NORSEMAN), pulls the stated LEVELS + IF-THEN SCENARIOS
    (``synthesis.newsletter_extract``), and idempotently upserts them keyed on
    (source, as_of[, name | idx]). Rule 7 (local model), rule 4 (descriptive).
    Skips a source cleanly when its note is missing / the model returns nothing.
    """
    from trading_intel.synthesis.newsletter_extract import extract_newsletter

    today = date.today()
    n = 0
    for key, label in _SIGNAL_SOURCES.items():
        note = session.execute(
            select(ResearchNote)
            .where(ResearchNote.symbol == key)
            .order_by(ResearchNote.as_of.desc())
        ).scalars().first()
        if note is None or not note.note_md:
            continue
        read = extract_newsletter(label, note.note_md, llm, model=model)
        if read.empty:
            continue
        for lv in read.levels:
            session.execute(
                pg_insert(NewsletterLevel)
                .values(
                    source=label, as_of=today, name=lv["name"],
                    value=lv["value"], unit=lv["unit"], note=lv["note"],
                )
                .on_conflict_do_update(
                    constraint="uq_nl_source_asof_name",
                    set_={"value": lv["value"], "unit": lv["unit"], "note": lv["note"]},
                )
            )
        for i, sc in enumerate(read.scenarios):
            session.execute(
                pg_insert(NewsletterScenario)
                .values(
                    source=label, as_of=today, idx=i, trigger=sc["trigger"],
                    consequence=sc["consequence"], direction=sc["direction"],
                    confidence=sc["confidence"],
                )
                .on_conflict_do_update(
                    constraint="uq_ns_source_asof_idx",
                    set_={
                        "trigger": sc["trigger"], "consequence": sc["consequence"],
                        "direction": sc["direction"], "confidence": sc["confidence"],
                    },
                )
            )
        n += 1
    session.commit()
    return n


def _fund_dir(root: Path, fund: str) -> Path:
    slug = "".join(ch if ch.isalnum() else "-" for ch in fund.lower()).strip("-")
    return root / (slug or "fund")


def run(
    session: Session,
    llm: LLMProvider,
    *,
    settings: Settings | None = None,
    letters_dir: Path | None = None,
) -> dict:
    """Fetch + save new letters, then ingest the letters tree into the watchlist."""
    settings = settings or get_settings()
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="letters_fetch")
    root = Path(letters_dir) if letters_dir is not None else DEFAULT_LETTERS_DIR

    saved = 0
    for src in substack_sources():
        try:
            xml = substack.fetch_feed(src.ref)
            entries = [e for e in substack.parse_feed(xml) if substack.is_letter(e)]
        except (httpx.HTTPError, OSError, ParseError) as exc:
            bound.warning("letters_fetch.feed_failed", fund=src.fund, err=str(exc))
            continue
        fund_dir = _fund_dir(root, src.fund)
        for entry in entries:
            if substack.save_entry(entry, fund_dir) is not None:
                saved += 1

    # Gmail lane (primary): pull allowlisted senders' letters + PDF attachments into the
    # same tree. No-op if the Gmail token/libs aren't provisioned (degrades cleanly).
    try:
        from trading_intel.letters import gmail_source

        saved += len(gmail_source.fetch_new(settings, root))
    except (OSError, ValueError) as exc:
        bound.warning("letters_fetch.gmail_failed", err=str(exc))

    result = ingest_folder(session, llm, research_dir=root, model=settings.LLM_TAGGING_MODEL)
    notes = _store_source_notes(session, root)
    signals = _extract_source_signals(session, llm, model=settings.LLM_TAGGING_MODEL)
    bound.info(
        "letters_fetch.done",
        saved=saved,
        ingested=result["ingested"],
        skipped=result["skipped"],
        new_symbols=result["new_symbols"],
        source_notes=notes,
        signals=signals,
    )
    return {"saved": saved, "source_notes": notes, "signals": signals, **result}


def main() -> None:
    """Manual entrypoint: wire Settings -> Ollama -> session, run once."""
    from trading_intel.memory.db import make_session_factory
    from trading_intel.synthesis.llm import OllamaProvider

    settings = get_settings()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    llm = OllamaProvider(settings)
    session_factory = make_session_factory(settings)
    with session_factory() as session:
        result = run(session, llm, settings=settings)
    print(f"Done: {result}")


if __name__ == "__main__":
    main()
