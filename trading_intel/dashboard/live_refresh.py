"""On-demand LIVE option-data pull for ONE symbol (persist via collectors).

Runs the equity EOD collectors once for a single symbol against a live Convex
source, writing fresh snapshots into the DB so the dashboard (which reads the DB)
shows the live pull inside the existing history - surface, charts, gamma all
update. Reuses the scheduled jobs via their ``symbols`` override (no logic
duplication). Descriptive collection only - FlashAlpha rule 4.
"""
from __future__ import annotations

import structlog
from sqlalchemy.orm import Session

from trading_intel.config import Settings
from trading_intel.scheduler.jobs import chain_snapshot, greeks_snapshot, oi_chain_eod

log = structlog.get_logger(__name__)

# greeks_snapshots (GEX/DEX/ATM IV) + oi_chain_eod (surface/gamma/IV-HV) +
# greeks_chain (per-strike). Flow is pulled separately by the report's live toggle.
_JOBS = (
    ("greeks", greeks_snapshot),
    ("oi_chain", oi_chain_eod),
    ("chain", chain_snapshot),
)


def pull_live_symbol(session: Session, symbol: str, *, settings: Settings) -> dict[str, str]:
    """Pull live Convex data for ``symbol`` via the collectors; per-job status.

    Each job is independent and commits its own write; a failure in one rolls
    that job back and moves on so a single bad pull doesn't block the rest.
    """
    from trading_intel.clients.convex import ConvexClient

    source = ConvexClient(settings)
    out: dict[str, str] = {}
    for name, job in _JOBS:
        try:
            job.run(session, source, settings=settings, symbols=[symbol])
            out[name] = "ok"
        except Exception as exc:  # noqa: BLE001 - one job failing must not block others
            session.rollback()
            out[name] = f"failed: {exc}"
            log.warning("live_refresh.job_failed", job=name, symbol=symbol, error=str(exc))
    return out
