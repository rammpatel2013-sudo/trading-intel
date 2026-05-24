"""Semantic retrieval over the ``chunks`` pgvector store.

Embeds a query with the same model used at ingest (nomic-embed-text via Ollama)
and runs a cosine-distance search against ``chunks``, joined to ``documents`` so
results can be filtered by ``kind`` (``methodology`` for the desk frameworks,
``research`` for company material). Returns the nearest chunks plus a formatted
grounding block (``format_kb``) suitable for dropping into an LLM prompt.

The vector SQL (``<=>`` operator, ``CAST(... AS vector)``) runs only against
Postgres/pgvector. The pure pieces — query embedding seam, row→hit mapping, and
``format_kb`` — are unit-tested with a fake session + fake LLM; the live cosine
search is exercised against the real DB.

Read-only and descriptive: retrieval surfaces reference notes, never signals
(FlashAlpha rule 4).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePath
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import TextClause

from trading_intel.memory.embeddings import format_vector

if TYPE_CHECKING:  # keep the Ollama-backed import out of runtime/test-collect
    from trading_intel.synthesis.llm import LLMProvider

log = structlog.get_logger(__name__)

DEFAULT_K = 6
DEFAULT_KB_MAX_CHARS = 6000


@dataclass(frozen=True)
class ChunkHit:
    """One retrieved chunk with its source document + cosine distance."""

    chunk_id: int
    document_id: int
    title: str
    text: str
    distance: float


_SELECT = (
    "SELECT c.id AS chunk_id, c.document_id AS document_id, d.path AS path, "
    "c.text AS text, c.embedding <=> CAST(:qvec AS vector) AS distance "
    "FROM chunks c JOIN documents d ON d.id = c.document_id WHERE d.kind = :kind"
)
_ORDER = " ORDER BY distance ASC LIMIT :k"

# Two fully-static statements (no string interpolation): one filters by symbol
# array overlap, one does not. Values are always bound parameters.
_SQL_BY_KIND = text(_SELECT + _ORDER)
_SQL_BY_KIND_SYMBOLS = text(_SELECT + " AND c.symbols && :symbols" + _ORDER).bindparams(
    bindparam("symbols")
)


def _search_sql(*, with_symbols: bool) -> TextClause:
    return _SQL_BY_KIND_SYMBOLS if with_symbols else _SQL_BY_KIND


def retrieve_chunks(
    session: Session,
    llm: LLMProvider,
    query: str,
    *,
    k: int = DEFAULT_K,
    kind: str = "methodology",
    symbols: list[str] | None = None,
    model: str | None = None,
) -> list[ChunkHit]:
    """Return the ``k`` chunks nearest to ``query`` (cosine), filtered by kind.

    ``symbols`` (optional) restricts to chunks tagged with any of the given
    tickers — useful for the ``research`` kind. Returns ``[]`` for an empty query.
    """
    if not query or not query.strip():
        return []

    qvec = llm.embed(query, model=model)[0]
    params: dict = {"qvec": format_vector(qvec), "kind": kind, "k": int(k)}
    if symbols:
        params["symbols"] = [s.upper() for s in symbols]

    stmt = _search_sql(with_symbols=bool(symbols))
    rows = session.execute(stmt, params).mappings().all()

    hits = [
        ChunkHit(
            chunk_id=int(r["chunk_id"]),
            document_id=int(r["document_id"]),
            title=PurePath(str(r["path"])).stem,
            text=str(r["text"]),
            distance=float(r["distance"]),
        )
        for r in rows
    ]
    log.info("retrieval.search", kind=kind, k=k, n_hits=len(hits), symbols=symbols or None)
    return hits


def format_kb(hits: list[ChunkHit], *, max_chars: int = DEFAULT_KB_MAX_CHARS) -> str:
    """Render retrieved chunks as a grounding block for an LLM prompt.

    Deduplicates repeated titles into one heading run and stops once ``max_chars``
    is reached so the context stays bounded for small local models.
    """
    if not hits:
        return ""
    parts: list[str] = []
    total = 0
    last_title: str | None = None
    for hit in hits:
        header = "" if hit.title == last_title else f"### {hit.title}\n"
        last_title = hit.title
        block = f"{header}{hit.text.strip()}\n"
        if total + len(block) > max_chars and parts:
            break
        parts.append(block)
        total += len(block)
    return "\n".join(parts).strip()
