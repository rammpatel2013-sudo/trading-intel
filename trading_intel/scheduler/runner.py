"""trading-intel — APScheduler runner.

This is the scheduler composition root. Instantiate clients here and pass
them into jobs.
"""
import logging

from apscheduler.schedulers.blocking import BlockingScheduler

from trading_intel.config import get_settings


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.LOG_LEVEL)
    log = logging.getLogger("trading_intel.scheduler")
    log.info("Starting trading-intel scheduler (env=%s)", settings.APP_ENV)

    scheduler = BlockingScheduler(timezone=settings.TZ)

    # Jobs will be registered here as they are built.
    # Example:
    # from trading_intel.scheduler.jobs import greeks_snapshot
    # scheduler.add_job(greeks_snapshot.run, "cron", hour=6, minute=45, name="greeks_snapshot")

    log.info("Scheduler started. Jobs registered: %d", len(scheduler.get_jobs()))
    scheduler.start()


if __name__ == "__main__":
    main()
