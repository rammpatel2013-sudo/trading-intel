"""Scheduled job: snapshot per-SPDR aggregate Greeks from CVForge (sector layer).

Runs the shared ``greeks_snapshot`` logic for the 11 SPDR sector ETFs using the
CVForge client (secondary source) — NOT Convex — so the sector layer never
spends the Convex 10/min budget reserved for the live regime engine (rule 1).
Writes one ``greeks_snapshots`` row per SPDR tagged ``source="cvforge"``: net
GEX / DEX / gex_flip / dex_flip / ATM IV + the flow enrichment, exactly like the
watchlist job. Feeds the sector lead/lag report (``api.sector`` /
``scripts/sector_report.py``).

Idempotent on ``(symbol, ts, source)`` (CLAUDE.md rule 5). Descriptor only
(FlashAlpha rule 4) — this is data collection, no signals.

Manual run:
    python -m trading_intel.scheduler.jobs.sector_greeks
"""
from __future__ import annotations

import structlog
from sqlalchemy.orm import Session

from trading_intel.clients import OptionsDataSource
from trading_intel.config import Settings, get_settings
from trading_intel.market.sector_correlation import SECTOR_SPDRS
from trading_intel.scheduler.jobs.greeks_snapshot import run as greeks_run

log = structlog.get_logger(__name__)

_SOURCE = "cvforge"


def run(
    session: Session,
    source: OptionsDataSource,
    *,
    settings: Settings | None = None,
    symbols: list[str] | None = None,
) -> None:
    """Snapshot the sector SPDRs' aggregate Greeks via ``source`` into greeks_snapshots.

    ``source`` should be a ``CVForgeClient`` (the secondary source) so this never
    touches the Convex budget. Delegates to the shared ``greeks_snapshot.run``
    with the SPDR universe and the ``cvforge`` source tag.
    """
    settings = settings or get_settings()
    roots = symbols or list(getattr(settings, "sector_roots", None) or SECTOR_SPDRS)
    greeks_run(session, source, settings=settings, symbols=roots, source_tag=_SOURCE)


def main() -> None:
    """Manual/scheduled entrypoint: wire Settings -> session -> CVForgeClient, run once."""
    from trading_intel.clients.cvforge import CVForgeClient
    from trading_intel.memory.db import make_session_factory

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    settings = get_settings()
    source = CVForgeClient(settings)
    session_factory = make_session_factory(settings)
    with session_factory() as session:
        run(session, source, settings=settings)


if __name__ == "__main__":
    main()
