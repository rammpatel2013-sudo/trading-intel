"""trading-intel — APScheduler runner.

This is the scheduler composition root. Instantiate clients here and pass
them into jobs.
"""
import logging

from apscheduler.schedulers.blocking import BlockingScheduler

from trading_intel.clients.convex import ConvexClient
from trading_intel.config import get_settings
from trading_intel.memory.db import make_session_factory
from trading_intel.scheduler.jobs import chain_snapshot, gex_rolling, greeks_snapshot


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.LOG_LEVEL)
    log = logging.getLogger("trading_intel.scheduler")
    log.info("Starting trading-intel scheduler (env=%s)", settings.APP_ENV)

    # Composition root: instantiate shared clients/session factory once.
    source = ConvexClient(settings)
    session_factory = make_session_factory(settings)

    def run_greeks_snapshot() -> None:
        with session_factory() as session:
            greeks_snapshot.run(session, source, settings=settings)

    def run_gex_rolling() -> None:
        with session_factory() as session:
            gex_rolling.run(session, source, settings=settings)

    def run_chain_snapshot() -> None:
        with session_factory() as session:
            chain_snapshot.run(session, source, settings=settings)

    scheduler = BlockingScheduler(timezone=settings.TZ)

    # Greeks snapshot — 06:45 ET pre-market (see MEMORY.md schedule).
    scheduler.add_job(run_greeks_snapshot, "cron", hour=6, minute=45, name="greeks_snapshot")
    # Per-strike chain snapshot — 06:45 ET pre-market (feeds day-over-day change panels).
    scheduler.add_job(run_chain_snapshot, "cron", hour=6, minute=45, name="chain_snapshot")
    # Long-dated rolling GEX — 16:30 ET EOD (heavier ~6-month pull, once daily).
    scheduler.add_job(run_gex_rolling, "cron", hour=16, minute=30, name="gex_rolling")

    log.info("Scheduler started. Jobs registered: %d", len(scheduler.get_jobs()))
    scheduler.start()


if __name__ == "__main__":
    main()
