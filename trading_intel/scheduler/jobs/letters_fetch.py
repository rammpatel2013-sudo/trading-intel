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
from pathlib import Path
from xml.etree.ElementTree import ParseError

import httpx
import structlog
from sqlalchemy.orm import Session

from trading_intel.config import Settings, get_settings
from trading_intel.letters import substack
from trading_intel.letters.sources import substack_sources
from trading_intel.memory.watchlist_ingest import ingest_folder
from trading_intel.synthesis.llm import LLMProvider

log = structlog.get_logger(__name__)

#: Letters live under the research tree so the nightly research ingest also sees them.
DEFAULT_LETTERS_DIR = Path("research/company/letters")


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
    bound.info(
        "letters_fetch.done",
        saved=saved,
        ingested=result["ingested"],
        skipped=result["skipped"],
        new_symbols=result["new_symbols"],
    )
    return {"saved": saved, **result}


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
