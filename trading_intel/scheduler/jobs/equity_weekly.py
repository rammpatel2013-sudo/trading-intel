"""Equity / swing weekly job - NAS DSM Saturday 07:15 ET: ``run_job.sh equity_weekly``.

Rebuilds the S&P 1500 membership (S&P 500 point-in-time from FMP's change log + today's IJH / IJR
holdings), refreshes profiles older than 30 days, pulls the latest quarterly statements, and
re-stamps market caps on the bars of any name that filed a new quarter. Idempotent (rule 5).
Manual: ``python -m trading_intel.scheduler.jobs.equity_weekly``.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_intel.config import Settings, get_settings
from trading_intel.equity import pipeline as pl
from trading_intel.memory.models_equity import EquityFundamental
from trading_intel.timeutils import eastern_now

log = structlog.get_logger(__name__)


def _latest_periods(session: Session) -> dict[str, date]:
    return dict(
        session.execute(
            select(EquityFundamental.symbol, func.max(EquityFundamental.period_end)).group_by(
                EquityFundamental.symbol
            )
        ).all()
    )


def run(
    session: Session,
    settings: Settings | None = None,
    *,
    src: Any = None,  # noqa: ANN401
    day: date | None = None,
) -> dict:
    settings = settings or get_settings()
    day = day or eastern_now().date()
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="equity_weekly")
    if src is None:
        from trading_intel.clients.equity_data import default_source

        src = default_source(settings)
    ivs = pl.refresh_universe(session, src, day)
    current = sorted({iv.symbol for iv in ivs if iv.end_date is None})
    n_prof = pl.refresh_profiles(session, src, pl.target_symbols(ivs), day)
    before = _latest_periods(session)
    new_names = [s for s in current if s not in before]
    pl.refresh_fundamentals(session, src, [s for s in current if s in before], limit=4)
    if new_names:  # names new to the universe need the full 12-quarter history
        pl.refresh_fundamentals(session, src, new_names, limit=12)
        pl.refresh_bars(session, src, new_names, day)
    after = _latest_periods(session)
    changed = [s for s, d in after.items() if before.get(s) != d]
    n_mc = pl.fill_market_caps(session, changed) if changed else 0
    bound.info(
        "equity_weekly.done",
        members=len(current),
        profiles=n_prof,
        new_names=len(new_names),
        new_quarters=len(changed),
        mcap_rows=n_mc,
    )
    return {"members": len(current), "new_names": len(new_names), "new_quarters": len(changed)}


def main() -> None:
    from trading_intel.memory.db import make_session_factory

    s = get_settings()
    with make_session_factory(s)() as session:
        print(run(session, s))


if __name__ == "__main__":
    main()
