"""One-off backfill for the equity / swing side (2026-11-14) - run ONCE on the NAS, in the background:

    sudo sh -c 'nohup bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_job.sh equity_backfill >/dev/null 2>&1 &'

(log: ~/ti_equity_backfill.log). Steps, each idempotent: 1 universe · 2 profiles · 3 fundamentals
(16 quarters) · 4 daily bars since 2023-01-01 · 5 market caps · 6 new highs / lows history ·
7 model + gate history + candidates · 8 the first equity page on the cockpit server (:8899/equity.html). About 10,000 FMP calls at 8/s
(~25 min) then a few minutes of maths. Resume from a step by hand:
``python -m trading_intel.scheduler.jobs.equity_backfill --from-step 4``.
"""

from __future__ import annotations

import argparse
import time

from sqlalchemy import func, select


def main(argv: list[str] | None = None) -> None:
    import structlog

    from trading_intel.clients.equity_data import default_source
    from trading_intel.config import get_settings
    from trading_intel.equity import pipeline as pl
    from trading_intel.equity import store
    from trading_intel.memory.db import make_session_factory
    from trading_intel.memory.models_equity import EquityBar
    from trading_intel.timeutils import eastern_now

    ap = argparse.ArgumentParser()
    ap.add_argument("--from-step", type=int, default=1)
    a = ap.parse_args(argv)
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    st = get_settings()
    src = default_source(st)
    today = eastern_now().date()
    t0 = time.time()

    def step(n: int, label: str) -> bool:
        if n < a.from_step:
            return False
        print(f"=== step {n}: {label} ({time.time() - t0:.0f}s) ===", flush=True)
        return True

    with make_session_factory(st)() as s:
        ivs = pl.refresh_universe(s, src, today) if step(1, "universe") else store.load_members(s)
        syms = pl.target_symbols(ivs)
        print(
            f"universe: {len(syms)} symbols (incl. names that left the S&P 500 since 2023)",
            flush=True,
        )
        if step(2, "profiles"):
            print("profiles:", pl.refresh_profiles(s, src, syms, today, max_age_days=0), flush=True)
        if step(3, "fundamentals"):
            print("fundamental rows:", pl.refresh_fundamentals(s, src, syms, limit=16), flush=True)
        if step(4, "bars"):
            print("bar rows:", pl.refresh_bars(s, src, syms, today), flush=True)
        if step(5, "market caps"):
            print("market-cap rows:", pl.fill_market_caps(s), flush=True)
        if step(6, "new highs / lows"):
            print("nh/nl:", pl.refresh_nhnl(s, today), flush=True)
        if step(7, "model + gate history"):
            res = pl.compute(
                s,
                today,
                start=pl.HISTORY_START,
                write_days=260,
                gate_days=800,
                factor_days=None,
                prune_days=0,
            )
            print("model:", {k: v for k, v in res.items() if k in ("day", "gate")}, flush=True)
        if step(8, "first report"):
            from trading_intel.scheduler.jobs import equity_daily

            last = s.execute(
                select(func.max(EquityBar.date)).where(EquityBar.symbol == "SPY")
            ).scalar()
            print(
                "report:",
                equity_daily.run(s, st, src=src, day=last),
                flush=True,
            )
    print(f"DONE in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
