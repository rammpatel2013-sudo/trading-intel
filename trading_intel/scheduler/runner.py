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
    delta_flow,
    flow_snapshot,
    gex_rolling,
    greeks_snapshot,
    index_skew,
    intraday_flow,
    iv_tenor_snapshots,
    live_gex,
    oi_chain_eod,
    prune_intraday,
    prune_live_gex,
    prune_oi_chain,
    prune_tas_prints,
    quotes_daily,
    skew_snapshots,
    tas_capture_job,
    vix_expirations,
    vix_options,
    vix_snapshot,
    vol_regime,
    vol_richness,
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

    def run_oi_chain_eod() -> None:
        with session_factory() as session:
            oi_chain_eod.run(session, source, settings=settings)

    def run_prune_oi_chain() -> None:
        with session_factory() as session:
            prune_oi_chain.run(session, settings=settings)

    def run_am_summary() -> None:
        with session_factory() as session:
            am_summary.run(session, llm, settings=settings)

    def run_vix_snapshot() -> None:
        from trading_intel.clients.cboe import CboeClient
        from trading_intel.clients.fred import FredClient

        with session_factory() as session:
            vix_snapshot.run(session, FredClient(settings), CboeClient())

    def run_vol_richness() -> None:
        with session_factory() as session:
            vol_richness.run(session, settings=settings)

    def run_skew_snapshots() -> None:
        with session_factory() as session:
            skew_snapshots.run(session, settings=settings)

    def run_iv_tenor_snapshots() -> None:
        with session_factory() as session:
            iv_tenor_snapshots.run(session, source, settings=settings)

    def run_vix_options() -> None:
        with session_factory() as session:
            vix_options.run(session, source, settings=settings)

    def run_vix_expirations() -> None:
        with session_factory() as session:
            vix_expirations.run(session, settings=settings)

    def run_index_skew() -> None:
        from trading_intel.clients.cboe import CboeClient

        with session_factory() as session:
            index_skew.run(session, CboeClient(), settings=settings)

    def run_vol_regime() -> None:
        with session_factory() as session:
            vol_regime.run(session, settings=settings)

    def run_delta_flow() -> None:
        with session_factory() as session:
            delta_flow.run(session, source, settings=settings)

    def run_live_gex() -> None:
        with session_factory() as session:
            live_gex.run(session, source, settings=settings)

    def run_prune_live_gex() -> None:
        with session_factory() as session:
            prune_live_gex.run(session, settings=settings)

    def run_tas_capture() -> None:
        with session_factory() as session:
            tas_capture_job.run(session, source, settings=settings)

    def run_prune_tas_prints() -> None:
        with session_factory() as session:
            prune_tas_prints.run(session, settings=settings)

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
    # Wide (~180d) EOD per-strike chain for the OI/flow change study — 16:35 ET,
    # just after gex_rolling (OI is an EOD figure). On the NAS this is a separate
    # DSM task (runner cron is ignored there).
    scheduler.add_job(run_oi_chain_eod, "cron", hour=16, minute=35, name="oi_chain_eod")
    # Constant-maturity forward IV for index ETFs (QQQ/SPY/SPX) — 16:38 ET. A LIVE
    # chain pull (these roots are excluded from the per-strike persisters), so it
    # only needs to run after the close, not after a stored-data job. Writes one
    # small aggregate row per (symbol, tenor). On the NAS this is a separate DSM
    # task (runner cron is ignored there).
    scheduler.add_job(
        run_iv_tenor_snapshots, "cron", hour=16, minute=38, name="iv_tenor_snapshots"
    )
    # Prune stale oi_chain_eod rows daily (retention via OI_CHAIN_RETENTION_DAYS).
    scheduler.add_job(run_prune_oi_chain, "cron", hour=2, minute=20, name="prune_oi_chain")
    # Daily AM regime report — 07:00 ET (after the 06:45 Greeks snapshot). Reads
    # stored data, renders via local LLM, upserts one am_summaries row/day. On the
    # NAS this is a separate DSM task (runner cron is ignored there).
    scheduler.add_job(run_am_summary, "cron", hour=7, minute=0, name="am_summary")
    # Daily VIX/VVIX/credit snapshot — 16:45 ET (FRED + CBOE). On the NAS this is
    # a separate DSM task (runner cron is ignored there).
    scheduler.add_job(run_vix_snapshot, "cron", hour=16, minute=45, name="vix_snapshot")
    # Daily vol-richness scan — 16:40 ET, after oi_chain_eod (reads the stored EOD
    # chain + quotes; no vendor call). On the NAS this is a separate DSM task.
    scheduler.add_job(run_vol_richness, "cron", hour=16, minute=40, name="vol_richness")
    # VIX options chain EOD pull -- 16:42 ET (after oi_chain_eod, before index_skew).
    # On the NAS this is a separate DSM task (runner cron is ignored there).
    scheduler.add_job(run_vix_options, "cron", hour=16, minute=42, name="vix_options")
    # Standard VIX expiration calendar refresh — 16:44 ET. Deterministic (no
    # vendor call); a sliding window of monthly settlement dates. NAS: separate
    # DSM task (runner cron is ignored there).
    scheduler.add_job(
        run_vix_expirations, "cron", hour=16, minute=44, name="vix_expirations"
    )
    # Index-level skew snapshot -- 16:50 ET (after vix_snapshot @ 16:45 and
    # vix_options @ 16:42, both of which it depends on). NAS: separate DSM task.
    scheduler.add_job(run_index_skew, "cron", hour=16, minute=50, name="index_skew")
    # Vol-regime classifier — 17:00 ET (after index_skew so today's row exists).
    # On the NAS this is a separate DSM task (runner cron is ignored there).
    scheduler.add_job(run_vol_regime, "cron", hour=17, minute=0, name="vol_regime")
    # Per-name skew snapshot -- 16:55 ET (after index_skew so SDEX delta is
    # available for the abnormal-RR residual). NAS: separate DSM task.
    scheduler.add_job(run_skew_snapshots, "cron", hour=16, minute=55, name="skew_snapshots")
    # Intraday all-expiry delta-notional flow — every 5 min during RTH (self-guards
    # to 09:30-16:00 weekdays). On the NAS this is a separate DSM task.
    scheduler.add_job(
        run_delta_flow, "cron", day_of_week="mon-fri", hour="9-16", minute="*/5",
        name="delta_flow",
    )
    # Intraday LIVE per-strike GEX (delta-band) — every 10 min during RTH; pruned
    # daily. On the NAS these are separate DSM tasks.
    scheduler.add_job(
        run_live_gex, "cron", day_of_week="mon-fri", hour="9-16", minute="*/10",
        name="live_gex",
    )
    scheduler.add_job(run_prune_live_gex, "cron", hour=2, minute=30, name="prune_live_gex")
    # Market-wide option tape capture — every minute during RTH (self-guards to
    # 09:30-16:00 weekdays). The unique print key dedupes overlap between polls.
    # On the NAS this is a separate DSM task (runner cron is ignored there).
    scheduler.add_job(
        run_tas_capture, "cron", day_of_week="mon-fri", hour="9-16", minute="*",
        name="tas_capture",
    )
    # Prune raw tas_prints older than TAS_RETENTION_DAYS (default 30) — 02:40 ET.
    scheduler.add_job(run_prune_tas_prints, "cron", hour=2, minute=40, name="prune_tas_prints")

    log.info("Scheduler started. Jobs registered: %d", len(scheduler.get_jobs()))
    scheduler.start()


if __name__ == "__main__":
    main()
