"""DSM job: snapshot ONLY the index roots' greeks (fast) — for the cockpit.

The cockpit reads the latest ``greeks_snapshots`` row per index symbol. The full
``greeks_snapshot`` job walks ~400 watchlist names and appends SPX/SPY/QQQ LAST,
so it is slow and, if interrupted, can leave the index rows unwritten — which
makes the cockpit show a stale (e.g. pre-open) index spot. This job snapshots
exactly the configured index roots (SPX/SPY/QQQ) via the SAME
``greeks_snapshot.run`` (so the row shape / dedup key are identical), giving the
cockpit a guaranteed-fresh index row in a few seconds. Zero watchlist symbols;
one ``exposures()`` call per index root. Descriptor only (rule 4).

Chain it before the cockpit in the DSM task:
    bash scripts/nas/run_job.sh index_greeks_snapshot cockpit_report

Manual run:
    python -m trading_intel.scheduler.jobs.index_greeks_snapshot
"""
from __future__ import annotations

import structlog
from sqlalchemy.orm import Session

from trading_intel.clients import OptionsDataSource
from trading_intel.config import Settings, get_settings
from trading_intel.scheduler.jobs import greeks_snapshot as gs

log = structlog.get_logger(__name__)

_FALLBACK_ROOTS = ["SPX", "SPY", "QQQ"]


def run(session: Session, source: OptionsDataSource, *, settings: Settings | None = None) -> None:
    """Snapshot ONLY the index roots (SPX/SPY/QQQ) into ``greeks_snapshots``."""
    settings = settings or get_settings()
    roots = list(getattr(settings, "index_roots", None) or _FALLBACK_ROOTS)
    log.bind(job="index_greeks_snapshot").info("index_greeks_snapshot.start", roots=roots)
    # Passing explicit symbols runs exactly those (no watchlist union), tagged 'convex'.
    gs.run(session, source, settings=settings, symbols=roots)


def main() -> None:
    from trading_intel.clients.convex import ConvexClient
    from trading_intel.memory.db import make_session_factory

    settings = get_settings()
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    source = ConvexClient(settings)
    with make_session_factory(settings)() as session:
        run(session, source, settings=settings)


if __name__ == "__main__":
    main()
