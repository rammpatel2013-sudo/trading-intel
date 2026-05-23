"""trading-intel — APScheduler runner.

This is the scheduler composition root. Instantiate clients here and pass
them into jobs.
"""
import logging

from apscheduler.schedulers.blocking import BlockingScheduler

from trading_intel.clients.convex import ConvexClient
from trading_intel.config import get_settings
from trading_intel.memory.db import make_session_factory
from trading_intel.scheduler.jobs import (
    am_summary,
    chain_snapshot,
    flow_snapshot,
    gex_rolling,
    greeks_snapshot,
    intraday_flow,
    prune_intraday,
    quotes_daily,
)
from trading_intel.synthesis.llm import OllamaProvider


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.LOG_LEVEL)
    log = logging.getLogger("trading_intel.scheduler")
    log.info("Starting trading-intel scheduler (env=%s)", settings.APP_ENV)

    # Composition root: instantiate shared clients/session factory once.
    source = ConvexClient(settings)
    llm = OllamaProvider(settings)
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

    def run_intraday_flow() -> None:
        with session_factory() as session:
            intraday_flow.run(session, source, settings=settings)

    def run_quotes_daily() -> None:
        from trading_intel.clients.prices import YFinancePriceSource

        with session_factory() as session:
            quotes_daily.run(session, YFinancePriceSource(), settings=settings)

    def run_flow_snapshot() -> None:
        with session_factory() as session:
            flow_snapshot.run(session, source, settings=settings)

    def run_prune_intraday() -> None:
        with session_factory() as session:
            prune_intraday.run(session, settings=settings)

    def run_am_summary() -> None:
        with session_factory() as session:
            am_summary.run(session, llm, settings=settings)

    scheduler = BlockingScheduler(timezone=settings.TZ)

    # Greeks snapshot — 06:45 ET pre-market (see MEMORY.md schedule).
    scheduler.add_job(run_greeks_snapshot, "cron", hour=6, minute=45, name="greeks_snapshot")
    # Per-strike chain snapshot — 06:45 ET pre-market (feeds day-over-day change panels).
    scheduler.add_job(run_chain_snapshot, "cron", hour=6, minute=45, name="chain_snapshot")
    # Long-dated rolling GEX — 16:30 ET EOD (heavier ~6-month pull, once daily).
    scheduler.add_job(run_gex_rolling, "cron", hour=16, minute=30, name="gex_rolling")
    # Intraday 0DTE/1DTE volume flow — every 5 min during RTH (ET); the job
    # self-guards to 09:30-16:00 on weekdays (see intraday_flow.is_market_hours).
    scheduler.add_job(
        run_intraday_flow,
        "cron",
        day_of_week="mon-fri",
        hour="9-16",
        minute="*/5",
        name="intraday_flow",
    )
    # Daily price history — 16:45 ET (after the close); appends the new
    # session and recomputes rv20/rv60. Idempotent upsert.
    scheduler.add_job(
        run_quotes_daily,
        "cron",
        day_of_week="mon-fri",
        hour=16,
        minute=45,
        name="quotes_daily",
    )
    # Options flow snapshot — every 30 min during RTH (ET); the job self-guards
    # to 09:30-16:00 on weekdays (intraday_flow.is_market_hours).
    scheduler.add_job(
        run_flow_snapshot,
        "cron",
        day_of_week="mon-fri",
        hour="9-16",
        minute="0,30",
        name="flow_snapshot",
    )
    # Prune stale intraday_flow rows hourly (retention via INTRADAY_RETENTION_HOURS).
    scheduler.add_job(run_prune_intraday, "cron", minute=5, name="prune_intraday")
    # Daily AM regime report — 07:00 ET (after the 06:45 Greeks snapshot). Reads
    # stored data, renders via local LLM, upserts one am_summaries row/day. On the
    # NAS this is a separate DSM task (runner cron is ignored there).
    scheduler.add_job(run_am_summary, "cron", hour=7, minute=0, name="am_summary")

    log.info("Scheduler started. Jobs registered: %d", len(scheduler.get_jobs()))
    scheduler.start()


if __name__ == "__main__":
    main()
