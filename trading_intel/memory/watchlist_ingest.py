"""Ingest an uploaded company-research file into the dynamic watchlist.

Extracts text (reusing the research pipeline's PDF/docx readers), records the
source ``Document`` (kind="research", deduped by SHA-256), runs the LLM watchlist
extractor, and idempotently upserts ``watchlist_entries`` (one row per
(symbol, source document)). Re-ingesting the same file is a no-op.

Provider-agnostic (takes an ``LLMProvider``). Descriptive context only — the
resulting watchlist carries rationale/sentiment, never a trade signal
(FlashAlpha rule 4). Research material may be proprietary — never export the
extracted text (CLAUDE.md rule 5).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_intel.errors import TradingIntelError
from trading_intel.memory.models import Document, WatchlistEntry
from trading_intel.memory.pdf_pipeline import extract_text, sha256_file
from trading_intel.synthesis.llm import LLMProvider
from trading_intel.synthesis.watchlist_extract import extract_watchlist

log = structlog.get_logger(__name__)

_SOURCE = "internal"
_MIN_USABLE_CHARS = 100
_UQ_COLS = ["symbol", "source_doc_id"]


def _get_or_create_document(session: Session, path: Path, *, sha: str, page_count: int) -> Document:
    existing = session.execute(
        select(Document).where(Document.sha256 == sha)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    doc = Document(
        path=str(path),
        source=_SOURCE,
        type=path.suffix.lower().lstrip("."),
        kind="research",
        sha256=sha,
        page_count=page_count or None,
    )
    session.add(doc)
    session.flush()  # assign doc.id
    return doc


def ingest_research(
    session: Session,
    llm: LLMProvider,
    path: Path,
    *,
    model: str | None = None,
) -> dict:
    """Ingest one research file into ``watchlist_entries``.

    Returns ``{"status": ingested|skipped|empty, "symbols": [...], "doc_id": int|None}``.
    """
    sha = sha256_file(path)
    existing = session.execute(
        select(Document).where(Document.sha256 == sha)
    ).scalar_one_or_none()
    if existing is not None:
        log.info("watchlist_ingest.skip_existing", file=path.name)
        return {"status": "skipped", "symbols": [], "doc_id": existing.id}

    text, page_count = extract_text(path)
    if len(text.strip()) < _MIN_USABLE_CHARS:
        log.warning("watchlist_ingest.empty_text", file=path.name)
        return {"status": "empty", "symbols": [], "doc_id": None}

    doc = _get_or_create_document(session, path, sha=sha, page_count=page_count)
    candidates = extract_watchlist(llm, path.stem, text, model=model)

    now = datetime.utcnow()
    records = [
        {
            "symbol": c.symbol[:16],
            "source_doc_id": doc.id,
            "rationale": c.rationale or None,
            "sentiment": c.sentiment,
            "confidence": c.confidence,
            "themes": c.themes or None,
            "added_at": now,
            "active": True,
        }
        for c in candidates
    ]
    if records:
        stmt = pg_insert(WatchlistEntry).values(records).on_conflict_do_nothing(
            index_elements=_UQ_COLS
        )
        session.execute(stmt)
    session.commit()
    symbols = [c.symbol for c in candidates]
    log.info("watchlist_ingest.ingested", file=path.name, symbols=symbols, doc_id=doc.id)
    return {"status": "ingested", "symbols": symbols, "doc_id": doc.id}


DEFAULT_RESEARCH_DIR = Path("research/company")


def discover_research_files(research_dir: Path) -> list[Path]:
    """Supported research files in ``research_dir`` (PDF/docx), sorted. [] if missing."""
    from trading_intel.memory.pdf_pipeline import SUPPORTED_EXTS

    if not research_dir.is_dir():
        return []
    return sorted(
        p for p in research_dir.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS
    )


def ingest_folder(
    session: Session,
    llm: LLMProvider,
    *,
    research_dir: Path = DEFAULT_RESEARCH_DIR,
    model: str | None = None,
) -> dict:
    """Ingest every NEW research file in ``research_dir`` into the watchlist.

    Returns ``{"ingested", "skipped", "empty", "failed", "new_symbols"}`` where
    ``new_symbols`` is the de-duplicated set of tickers surfaced by the files
    ingested on this run (for an immediate price backfill).
    """
    files = discover_research_files(research_dir)
    log.info("watchlist_ingest.folder_start", n_files=len(files), dir=str(research_dir))
    stats = {"ingested": 0, "skipped": 0, "empty": 0, "failed": 0}
    new_symbols: list[str] = []
    seen: set[str] = set()
    for path in files:
        try:
            result = ingest_research(session, llm, path, model=model)
        except (TradingIntelError, OSError, ValueError) as exc:
            session.rollback()
            stats["failed"] += 1
            log.warning("watchlist_ingest.folder_failed", file=path.name, error=str(exc))
            continue
        stats[result["status"]] = stats.get(result["status"], 0) + 1
        if result["status"] == "ingested":
            for sym in result["symbols"]:
                if sym not in seen:
                    seen.add(sym)
                    new_symbols.append(sym)
    out = {**stats, "new_symbols": new_symbols}
    log.info("watchlist_ingest.folder_done", **stats, new_symbols=new_symbols)
    return out


def main() -> None:
    """CLI: ingest an uploaded research file into the dynamic watchlist."""
    import argparse

    from trading_intel.config import get_settings
    from trading_intel.memory.db import make_session_factory
    from trading_intel.synthesis.llm import OllamaProvider

    parser = argparse.ArgumentParser(description="Ingest research -> dynamic watchlist.")
    parser.add_argument("path", help="Path to the research PDF/docx to ingest")
    parser.add_argument("--model", default=None, help="Ollama model override")
    args = parser.parse_args()

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    settings = get_settings()
    llm = OllamaProvider(settings)
    session_factory = make_session_factory(settings)
    with session_factory() as session:
        result = ingest_research(session, llm, Path(args.path), model=args.model)
    print(f"Done: {result}")


if __name__ == "__main__":
    main()
