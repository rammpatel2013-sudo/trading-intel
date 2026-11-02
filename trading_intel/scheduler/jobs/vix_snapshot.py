"""Daily VIX / VVIX / term-structure / credit snapshot job -> ``vix_data``.

Pulls the SAME-DAY VIX close, VVIX and the index term structure (VIX9D/VIX3M/VIX6M)
from CBOE (VIX's trailing-20 stdev from the stored history), HY/IG credit OAS from
FRED filed under each observation's own date (FRED posts a day late), classifies the VIX regime
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
from trading_intel.timeutils import eastern_now, is_trading_session

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


#: FRED observations re-applied by date on every run (catches FRED's one-day lag).
_CREDIT_BACKFILL_OBS = 10


def _dated(series: pd.Series | None) -> dict[date, float]:
    """``{observation date: value}`` for a FRED series; ``{}`` when it carries no dates.

    FRED posts each day's value the NEXT day, so a value can only be filed under the
    date it belongs to — never under the run date (the pre-2026-10-29 bug: every
    ``vix`` / ``hy_oas`` / ``ig_oas`` sat one session late).
    """
    if series is None or series.empty:
        return {}
    idx = series.index
    if not isinstance(idx, pd.DatetimeIndex):
        try:
            idx = pd.DatetimeIndex(idx)
        except (TypeError, ValueError):
            return {}
        if idx.year.min() < 1990:  # a RangeIndex coerced to epoch nanoseconds — not dates
            return {}
    return {ts.date(): float(v) for ts, v in zip(idx, series.to_numpy()) if v == v}


def _fred_same_day_vix(fred: FredClient, as_of: date) -> float | None:
    """FRED VIXCLS for ``as_of`` — only if FRED already has that date (it usually doesn't)."""
    try:
        from trading_intel.clients.fred import VIX_SERIES

        return _dated(fred.series(VIX_SERIES)).get(as_of)
    except Exception as exc:  # noqa: BLE001 — fallback only
        log.warning("vix_snapshot.fred_vix_failed", error=str(exc))
        return None


def _apply_credit(session: Session, fred: FredClient, as_of: date) -> tuple[float | None, float | None]:
    """File FRED HY/IG OAS under their own observation dates; return ``as_of``'s pair.

    The last ``_CREDIT_BACKFILL_OBS`` observations are re-applied to rows that exist,
    so yesterday's row gets yesterday's spread when FRED posts it this morning.
    """
    from trading_intel.clients.fred import HY_OAS_SERIES, IG_OAS_SERIES

    try:
        hy = _dated(fred.series(HY_OAS_SERIES))
        ig = _dated(fred.series(IG_OAS_SERIES))
    except Exception as exc:  # noqa: BLE001 — credit is best-effort
        log.warning("vix_snapshot.credit_failed", error=str(exc))
        return None, None
    recent = sorted(set(hy) | set(ig))[-_CREDIT_BACKFILL_OBS:]
    for d in recent:
        if d == as_of:
            continue
        prior = session.get(VixData, d)
        if prior is not None:
            prior.hy_oas = hy.get(d, prior.hy_oas)
            prior.ig_oas = ig.get(d, prior.ig_oas)
    return hy.get(as_of), ig.get(as_of)


def run(session: Session, fred: FredClient, cboe: CboeClient, *, as_of: date | None = None) -> None:
    """Fetch today's macro vol snapshot and upsert it into ``vix_data``.

    VIX is Cboe's SAME-DAY close (the ``_VIX`` quote, run after 16:15 ET). FRED's
    VIXCLS posts a day late, so it is only a fallback when it already carries
    ``as_of`` — otherwise ``vix`` stays empty rather than filing yesterday's close
    under today (2026-10-29 fix: the 10/23 row held 19.03, the 10/22 close; the
    real 10/23 close was 19.45).
    """
    as_of = as_of or eastern_now().date()
    if not is_trading_session(as_of):
        # A holiday run would file the last session's Cboe close under a closed day.
        log.info("vix_snapshot.skipped_non_session", as_of=as_of.isoformat())
        return
    # First: a failed rv20 read rolls the session back, which would undo the credit
    # back-fill below if it ran later.
    rv20 = _latest_realized_vol(session)
    vvix = cboe.vvix()
    term = _term_structure(cboe)
    vix = term.get("VIX")
    if vix is None:
        vix = _fred_same_day_vix(fred, as_of)
        log.warning("vix_snapshot.cboe_vix_missing", fallback=vix)
    hy_oas, ig_oas = _apply_credit(session, fred, as_of)

    def _sd20(col, today_val: float | None) -> float | None:
        prior = list(
            session.execute(
                select(col).where(VixData.date < as_of).order_by(VixData.date.desc()).limit(19)
            ).scalars()
        )
        window = [v for v in [today_val, *prior] if v is not None]
        return float(pd.Series(window).std(ddof=0)) if len(window) >= 2 else None

    vix_sd20 = _sd20(VixData.vix, vix)
    vvix_sd20 = _sd20(VixData.vvix, vvix)

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
