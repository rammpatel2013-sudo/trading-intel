"""Effective watchlist resolution.

The collectors and dashboards historically tracked only the static ``WATCHLIST``
from ``.env``. The research-ingest pipeline can also surface tickers (into
``watchlist_entries``); ``effective_symbols`` unions the two so a name pulled
from an uploaded research report automatically gets price history and the full
regime-data collection — and shows up on the dashboards — without editing
``.env``.

Static symbols come first (preserving order); active research tickers are
appended if not already present. If ``watchlist_entries`` is unavailable (e.g.
the migration hasn't run, or in a SQLite unit test), it degrades gracefully to
the static list.
"""

from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from trading_intel.config import Settings
from trading_intel.memory.models import WatchlistEntry

log = structlog.get_logger(__name__)


def research_symbols(session: Session) -> list[str]:
    """Distinct active symbols from the research-driven watchlist (or [])."""
    try:
        rows = (
            session.execute(
                select(WatchlistEntry.symbol).where(WatchlistEntry.active.is_(True)).distinct()
            )
            .scalars()
            .all()
        )
    except (SQLAlchemyError, AttributeError, TypeError) as exc:
        # Table missing / DB not migrated yet, or a non-DB session (unit-test
        # recording fake) — degrade to the static watchlist.
        rollback = getattr(session, "rollback", None)
        if callable(rollback):
            rollback()
        log.debug("effective_symbols.research_unavailable", error=str(exc))
        return []
    return [str(s).strip().upper() for s in rows if s and str(s).strip()]


def effective_symbols(session: Session, settings: Settings) -> list[str]:
    """Static watchlist plus active research-watchlist tickers (static first)."""
    out = list(settings.watchlist_symbols)
    seen = set(out)
    for sym in research_symbols(session):
        if sym not in seen:
            out.append(sym)
            seen.add(sym)
    return out
