"""One-time cleanup: strip exchange suffixes (.N / .TO / ...) from stored
research-watchlist symbols.

New research ingests are already normalized (``watchlist_extract.normalize_symbol``
runs in ``parse_candidates``), but rows ingested *before* that fix still carry the
suffix (e.g. ``RY.TO``, ``AAPL.N``) — which breaks the US price lookup and shows
ugly tickers on the Research Watchlist page. This rewrites those rows in place to
the base ticker, dropping any row that would collide with an already-normalized
entry for the same source document.

Idempotent — safe to re-run (already-clean symbols are skipped). Run once after
deploying the normalize fix:

    python scripts/normalize_watchlist_symbols.py
"""

from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.memory.models import WatchlistEntry
from trading_intel.synthesis.watchlist_extract import normalize_symbol

log = structlog.get_logger(__name__)


def run(session: Session) -> dict[str, int]:
    """Normalize stored ``watchlist_entries`` symbols. Returns a summary dict."""
    rows = list(session.execute(select(WatchlistEntry)).scalars())
    # natural key is (symbol, source_doc_id); track it to avoid creating dups.
    keys = {(r.symbol, r.source_doc_id) for r in rows}
    updated = deleted = 0
    for r in rows:
        norm = normalize_symbol(r.symbol)
        if norm == r.symbol:
            continue
        if (norm, r.source_doc_id) in keys:
            # a normalized entry for the same doc already exists -> drop the dup.
            session.delete(r)
            keys.discard((r.symbol, r.source_doc_id))
            deleted += 1
        else:
            keys.discard((r.symbol, r.source_doc_id))
            r.symbol = norm
            keys.add((norm, r.source_doc_id))
            updated += 1
    session.commit()
    return {"total": len(rows), "updated": updated, "deleted": deleted}


def main() -> None:
    from trading_intel.config import get_settings
    from trading_intel.memory.db import make_session_factory

    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    session_factory = make_session_factory(get_settings())
    with session_factory() as session:
        summary = run(session)
    log.info("normalize_watchlist_symbols.done", **summary)
    print(
        f"watchlist symbols normalized: {summary['updated']} updated, "
        f"{summary['deleted']} dup(s) removed, of {summary['total']} rows."
    )


if __name__ == "__main__":
    main()
