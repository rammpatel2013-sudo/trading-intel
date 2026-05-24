"""Embedding persistence for the ``chunks`` pgvector store.

This is the single seam where document text becomes searchable vectors. It is
deliberately *not* wired through the ORM: the ``chunks.embedding`` column is a
pgvector ``vector`` type added in migration 0001, and mapping it on the ORM model
would force a Postgres-only import into ``models.py`` (breaking SQLite test
collection). Instead we write/read the embedding via parameterised raw SQL with
an explicit ``CAST(... AS vector)``, so ``models.py`` stays vendor-neutral and
this module is the only place that touches pgvector.

Used by ``memory/pdf_pipeline`` (embed on ingest) and ``memory/sync_knowledge``
(supersede / backfill). All callers pass an ``LLMProvider`` (Ollama today) — no
direct vendor import here.
"""

from __future__ import annotations

from datetime import date as _date
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from trading_intel.memory.chunking import chunk_text

if TYPE_CHECKING:  # avoid importing the Ollama-backed module at runtime/test-collect
    from trading_intel.synthesis.llm import LLMProvider

log = structlog.get_logger(__name__)

_INSERT_CHUNK = text(
    "INSERT INTO chunks (document_id, chunk_idx, text, embedding, theme_ids, symbols, date) "
    "VALUES (:document_id, :chunk_idx, :text, CAST(:embedding AS vector), "
    ":theme_ids, :symbols, :date)"
).bindparams(bindparam("theme_ids"), bindparam("symbols"))

_DELETE_CHUNKS = text("DELETE FROM chunks WHERE document_id = :document_id")
_COUNT_CHUNKS = text("SELECT COUNT(*) FROM chunks WHERE document_id = :document_id")


def format_vector(vec: list[float]) -> str:
    """Render an embedding as a pgvector literal, e.g. ``[0.1,0.2,0.3]``."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def delete_chunks(session: Session, document_id: int) -> None:
    """Remove every chunk for a document (used when superseding/pruning)."""
    session.execute(_DELETE_CHUNKS, {"document_id": document_id})


def count_chunks(session: Session, document_id: int) -> int:
    return int(session.execute(_COUNT_CHUNKS, {"document_id": document_id}).scalar_one())


def embed_and_store_chunks(
    session: Session,
    llm: LLMProvider,
    *,
    document_id: int,
    text_body: str,
    theme_ids: list[int] | None = None,
    symbols: list[str] | None = None,
    obs_date: _date | None = None,
    model: str | None = None,
    target_chars: int | None = None,
    overlap: int | None = None,
) -> int:
    """Chunk ``text_body``, embed each chunk, and insert rows into ``chunks``.

    Returns the number of chunks written. Embeds the whole document (callers pass
    the *full* extracted text, not the truncated playbook slice). The embedding
    call may raise if the LLM/embedding backend is unavailable — callers decide
    whether to treat that as fatal (it is non-fatal during ingest).
    """
    kwargs: dict = {}
    if target_chars is not None:
        kwargs["target_chars"] = target_chars
    if overlap is not None:
        kwargs["overlap"] = overlap
    chunks = chunk_text(text_body, **kwargs)
    if not chunks:
        return 0

    vectors = llm.embed(chunks, model=model)
    if len(vectors) != len(chunks):
        raise ValueError(
            f"embed returned {len(vectors)} vectors for {len(chunks)} chunks (doc {document_id})"
        )

    rows = [
        {
            "document_id": document_id,
            "chunk_idx": idx,
            "text": chunk,
            "embedding": format_vector(vec),
            "theme_ids": theme_ids or None,
            "symbols": symbols or None,
            "date": obs_date,
        }
        for idx, (chunk, vec) in enumerate(zip(chunks, vectors, strict=True))
    ]
    session.execute(_INSERT_CHUNK, rows)
    log.info("embeddings.stored", document_id=document_id, chunks=len(rows))
    return len(rows)
