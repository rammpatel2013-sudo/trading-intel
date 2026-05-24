"""Folder sync + re-indexing for the knowledge base.

The ingest pipelines dedupe by content hash, so an *edited* document silently
piles up a second copy and chunks for a deleted file linger forever. This module
adds the missing lifecycle: it scans a drop folder and reconciles it against the
``documents`` rows matched **by path**:

- new file (path not seen)            -> ingest
- unchanged (same path + same hash)   -> skip (but backfill embeddings if the
                                          document has no chunks yet)
- edited (same path, new hash)        -> supersede: delete the old document's
                                          chunks / theme observations / watchlist
                                          entries + its playbook, then re-ingest
- file removed from the folder         -> prune the same way (opt-in)

Two knowledge kinds are handled:
- ``methodology`` (``research/doc/``)  -> full ingest with chunk embeddings +
                                          playbooks (the RAG substrate)
- ``research`` (``research/company/``) -> watchlist extraction (the Type-2
                                          company layer; no embeddings yet)

Chunk reads/writes go through the ``embeddings`` seam so this module is testable
on SQLite (the pgvector ``chunks`` table is never created there). Needs Ollama
for embeddings — run on the laptop. Descriptive knowledge only (rule 4).
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from trading_intel.memory.embeddings import count_chunks, delete_chunks, embed_and_store_chunks
from trading_intel.memory.models import Document, ThemeObservation, WatchlistEntry
from trading_intel.memory.pdf_pipeline import (
    discover_documents,
    extract_text,
    ingest_document,
    sha256_file,
    slugify,
)
from trading_intel.memory.watchlist_ingest import discover_research_files, ingest_research

if TYPE_CHECKING:
    from trading_intel.synthesis.llm import LLMProvider

log = structlog.get_logger(__name__)

METHODOLOGY_DIR = Path("research/doc")
COMPANY_DIR = Path("research/company")
PLAYBOOK_DIR = Path("docs/playbooks")


def _empty_stats() -> dict[str, int]:
    return {"new": 0, "updated": 0, "unchanged": 0, "backfilled": 0, "pruned": 0, "failed": 0}


def _documents_by_path(session: Session, kind: str) -> dict[str, list[Document]]:
    rows = session.execute(select(Document).where(Document.kind == kind)).scalars().all()
    out: dict[str, list[Document]] = {}
    for doc in rows:
        out.setdefault(doc.path, []).append(doc)
    return out


def _delete_document(session: Session, doc: Document, *, playbook_dir: Path | None) -> None:
    """Remove a document and everything derived from it (chunks/obs/entries/playbook)."""
    try:
        delete_chunks(session, doc.id)
    except Exception as exc:  # chunks table may be absent (e.g. tests) — non-fatal
        log.debug("sync.delete_chunks_skipped", doc_id=doc.id, error=str(exc))
    session.execute(delete(ThemeObservation).where(ThemeObservation.source_doc_id == doc.id))
    session.execute(delete(WatchlistEntry).where(WatchlistEntry.source_doc_id == doc.id))
    if playbook_dir is not None:
        playbook = playbook_dir / f"{slugify(Path(doc.path).stem)}.md"
        try:
            playbook.unlink(missing_ok=True)
        except OSError as exc:
            log.debug("sync.playbook_unlink_failed", path=str(playbook), error=str(exc))
    session.delete(doc)
    session.flush()


def _backfill_embeddings(
    session: Session, llm: LLMProvider, doc: Document, *, model: str | None
) -> int:
    """Embed a document that exists but has no chunks (e.g. ingested pre-RAG)."""
    obs = session.execute(
        select(ThemeObservation).where(ThemeObservation.source_doc_id == doc.id)
    ).scalars().all()
    theme_ids = sorted({o.theme_id for o in obs})
    symbols = sorted({o.symbol for o in obs if o.symbol})
    text_body, _ = extract_text(Path(doc.path))
    return embed_and_store_chunks(
        session,
        llm,
        document_id=doc.id,
        text_body=text_body,
        theme_ids=theme_ids or None,
        symbols=symbols or None,
        obs_date=date.today(),
        model=model,
    )


def sync_methodology(
    session: Session,
    llm: LLMProvider,
    *,
    research_dir: Path = METHODOLOGY_DIR,
    playbook_dir: Path = PLAYBOOK_DIR,
    model: str | None = None,
    embed: bool = True,
    prune_removed: bool = False,
) -> dict[str, int]:
    """Reconcile the methodology drop folder against the ``documents`` table."""
    stats = _empty_stats()
    files = discover_documents(research_dir) if research_dir.is_dir() else []
    by_path = _documents_by_path(session, "methodology")
    seen: set[str] = set()

    for path in files:
        sp = str(path)
        seen.add(sp)
        try:
            sha = sha256_file(path)
            existing = by_path.get(sp, [])
            match = next((d for d in existing if d.sha256 == sha), None)
            if match is not None:
                if embed and count_chunks(session, match.id) == 0:
                    try:
                        if _backfill_embeddings(session, llm, match, model=model):
                            session.commit()
                            stats["backfilled"] += 1
                        else:
                            stats["unchanged"] += 1
                    except Exception as exc:  # embedding backend down — leave for next run
                        session.rollback()
                        log.warning("sync.backfill_failed", file=path.name, error=str(exc))
                        stats["unchanged"] += 1
                else:
                    stats["unchanged"] += 1
                continue

            if existing:  # same path, new hash -> edited
                for old in existing:
                    _delete_document(session, old, playbook_dir=playbook_dir)
                status = ingest_document(
                    session, llm, path, playbook_dir=playbook_dir, model=model, embed=embed
                )
                stats["updated" if status == "ingested" else "failed"] += 1
            else:
                status = ingest_document(
                    session, llm, path, playbook_dir=playbook_dir, model=model, embed=embed
                )
                stats["new" if status == "ingested" else "unchanged"] += (
                    1 if status == "ingested" else 0
                )
        except Exception as exc:
            session.rollback()
            stats["failed"] += 1
            log.warning("sync.methodology_failed", file=path.name, error=str(exc))

    if prune_removed:
        for sp, docs in by_path.items():
            if sp in seen:
                continue
            for old in docs:
                _delete_document(session, old, playbook_dir=playbook_dir)
                stats["pruned"] += 1
        session.commit()

    log.info("sync.methodology_done", dir=str(research_dir), **stats)
    return stats


def sync_research(
    session: Session,
    llm: LLMProvider,
    *,
    research_dir: Path = COMPANY_DIR,
    model: str | None = None,
    prune_removed: bool = False,
) -> dict[str, int]:
    """Reconcile the company-research folder against ``documents`` + watchlist."""
    stats = _empty_stats()
    new_symbols: list[str] = []
    files = discover_research_files(research_dir)
    by_path = _documents_by_path(session, "research")
    seen: set[str] = set()

    for path in files:
        sp = str(path)
        seen.add(sp)
        try:
            sha = sha256_file(path)
            existing = by_path.get(sp, [])
            match = next((d for d in existing if d.sha256 == sha), None)
            if match is not None:
                stats["unchanged"] += 1
                continue
            edited = bool(existing)
            for old in existing:  # supersede old watchlist entries for this file
                _delete_document(session, old, playbook_dir=None)
            result = ingest_research(session, llm, path, model=model)
            if result["status"] == "ingested":
                stats["updated" if edited else "new"] += 1
                new_symbols.extend(result["symbols"])
        except Exception as exc:
            session.rollback()
            stats["failed"] += 1
            log.warning("sync.research_failed", file=path.name, error=str(exc))

    if prune_removed:
        for sp, docs in by_path.items():
            if sp in seen:
                continue
            for old in docs:
                _delete_document(session, old, playbook_dir=None)
                stats["pruned"] += 1
        session.commit()

    out = {**stats, "new_symbols": sorted(set(new_symbols))}
    log.info("sync.research_done", dir=str(research_dir), **stats, new_symbols=out["new_symbols"])
    return out


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan the research drop folders and re-index changed knowledge."
    )
    parser.add_argument("--methodology-dir", default=str(METHODOLOGY_DIR))
    parser.add_argument("--company-dir", default=str(COMPANY_DIR))
    parser.add_argument("--playbook-dir", default=str(PLAYBOOK_DIR))
    parser.add_argument("--model", default=None, help="Ollama model override")
    parser.add_argument("--no-embed", dest="embed", action="store_false")
    parser.add_argument(
        "--prune-removed",
        action="store_true",
        help="Also delete knowledge for files no longer present in a folder.",
    )
    parser.add_argument("--skip-methodology", action="store_true")
    parser.add_argument("--skip-research", action="store_true")
    args = parser.parse_args()

    from trading_intel.config import get_settings
    from trading_intel.memory.db import make_session_factory
    from trading_intel.synthesis.llm import OllamaProvider

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
    results: dict[str, dict] = {}
    with session_factory() as session:
        if not args.skip_methodology:
            results["methodology"] = sync_methodology(
                session,
                llm,
                research_dir=Path(args.methodology_dir),
                playbook_dir=Path(args.playbook_dir),
                model=args.model,
                embed=args.embed,
                prune_removed=args.prune_removed,
            )
        if not args.skip_research:
            results["research"] = sync_research(
                session,
                llm,
                research_dir=Path(args.company_dir),
                model=args.model,
                prune_removed=args.prune_removed,
            )
    print(f"Done: {results}")


if __name__ == "__main__":
    main()
