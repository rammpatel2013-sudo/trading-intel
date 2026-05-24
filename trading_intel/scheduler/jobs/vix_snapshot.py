"""Daily VIX / VVIX / term-structure / credit snapshot job -> ``vix_data``.

Pulls the VIX close + trailing-20 stdev and HY/IG credit OAS from FRED, VVIX and
the index term structure (VIX9D/VIX3M/VIX6M) from CBOE, classifies the VIX regime
zone, and computes the variance risk premium (VRP = VIX - SPX 20-day realized
vol, in vol points) from the stored ``quotes_daily.rv20``. Upserts one row per
date. VVIX's 20-day stdev is computed from the stored history (CBOE only gives
the live level). Idempotent: re-running for the same date updates that row in
place (CLAUDE.md rule 5). Descriptive data only — no signals (rule 4).

Run manually (FRED key in .env; CBOE reached over HTTP):
    python -m trading_intel.scheduler.jobs.vix_snapshot
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import structlog
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from trading_intel.clients.cboe import CboeClient
from trading_intel.clients.fred import FredClient
from trading_intel.dashboard.vix_view import classify_zone
from trading_intel.memory.models import QuoteDaily, VixData

log = structlog.get_logger(__name__)

#: Underlying whose realized vol anchors the variance risk premium (VRP = VIX - RV).
VRP_SYMBOL = "SPX"


def _latest_realized_vol(session: Session, *, symbol: str = VRP_SYMBOL) -> float | None:
    """Most recent stored 20-day realized vol (decimal) for ``symbol``.

    Returns ``None`` when there is no row, no ``rv20``, or the table is absent
    (e.g. a unit-test session that only created ``vix_data``).
    """
    try:
        val = session.execute(
            select(QuoteDaily.rv20)
            .where(QuoteDaily.symbol == symbol, QuoteDaily.rv20.is_not(None))
            .order_by(QuoteDaily.date.desc())
            .limit(1)
        ).scalar_one_or_none()
    except SQLAlchemyError:
        # A failed read (e.g. missing table) leaves the transaction needing a
        # rollback before the snapshot write can proceed. VRP is best-effort.
        session.rollback()
        return None
    return float(val) if val is not None else None


def _term_structure(cboe: CboeClient) -> dict[str, float | None]:
    """CBOE index term structure, degrading to empty on any fetch/shape failure."""
    try:
        return cboe.term_structure() or {}
    except Exception as exc:  # a CBOE outage / shape change must not kill the snapshot
        log.warning("vix_snapshot.term_structure_failed", error=str(exc))
        return {}


def run(session: Session, fred: FredClient, cboe: CboeClient, *, as_of: date | None = None) -> None:
    """Fetch today's macro vol snapshot and upsert it into ``vix_data``."""
    as_of = as_of or date.today()
    vix, vix_sd20 = fred.vix_with_sd20()
    hy_oas, ig_oas = fred.credit_spreads()
    vvix = cboe.vvix()
    term = _term_structure(cboe)

    prior_vvix = list(
        session.execute(
            select(VixData.vvix).order_by(VixData.date.desc()).limit(19)
        ).scalars()
    )
    vvix_window = [v for v in [vvix, *prior_vvix] if v is not None]
    vvix_sd20 = float(pd.Series(vvix_window).std(ddof=0)) if len(vvix_window) >= 2 else None

    rv20 = _latest_realized_vol(session)
    vrp = (vix - rv20 * 100.0) if (vix is not None and rv20 is not None) else None

    row = session.get(VixData, as_of)
    if row is None:
        row = VixData(date=as_of)
        session.add(row)
    row.vix = vix
    row.vvix = vvix
    row.move = None  # MOVE is not freely available on FRED — left unset
    row.hy_oas = hy_oas
    row.ig_oas = ig_oas
    row.vix_sd20 = vix_sd20
    row.vvix_sd20 = vvix_sd20
    row.vega_zone = classify_zone(vix)
    row.vix9d = term.get("VIX9D")
    row.vix3m = term.get("VIX3M")
    row.vix6m = term.get("VIX6M")
    row.vrp = vrp
    session.commit()
    log.info(
        "vix_snapshot.done",
        date=as_of.isoformat(), vix=vix, vvix=vvix, zone=row.vega_zone,
        hy_oas=hy_oas, ig_oas=ig_oas,
        vix9d=row.vix9d, vix3m=row.vix3m, vix6m=row.vix6m, vrp=vrp,
    )


def main() -> None:
    from trading_intel.config import get_settings
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
    fred = FredClient(settings)
    cboe = CboeClient()
    session_factory = make_session_factory(settings)
    with session_factory() as session:
        run(session, fred, cboe)


if __name__ == "__main__":
    main()
