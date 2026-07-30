"""Scheduled job: snapshot the sector-ETF realized-correlation regime.

Pulls daily closes for the 11 SPDR sector ETFs from the yfinance price source
(free — no IBKR), computes 21d/63d average PAIRWISE correlation + cross-sectional
dispersion via ``market.sector_correlation`` (pure math), and writes one
``sector_corr_snapshots`` row. This is the *realized* complement to the
option-implied COR1M/COR3M already banked in the VIX complex. Descriptor only
(FlashAlpha rule 4). Idempotent on ``(as_of, source)``.

Manual run:
    python -m trading_intel.scheduler.jobs.sector_corr
"""
from __future__ import annotations

import uuid
from datetime import date

import pandas as pd
import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_intel.clients.prices import YFinancePriceSource
from trading_intel.config import Settings, get_settings
from trading_intel.market.sector_correlation import SECTOR_SPDRS, latest_snapshot
from trading_intel.memory.models import SectorCorrSnapshot
from trading_intel.timeutils import eastern_now

log = structlog.get_logger(__name__)
_SOURCE = "yfinance"


def _close_frame(source: YFinancePriceSource, symbols: tuple[str, ...], *, period: str) -> pd.DataFrame:
    """Wide daily-close frame (index=date, cols=ticker) for ``symbols``; best-effort."""
    cols: dict[str, pd.Series] = {}
    for sym in symbols:
        try:
            hist = source.daily_history(sym, period=period)
        except Exception as exc:  # noqa: BLE001 — one bad ticker shouldn't kill the snapshot
            log.warning("sector_corr.history_failed", symbol=sym, error=str(exc))
            continue
        if hist is None or hist.empty or "close" not in hist.columns:
            continue
        cols[sym] = hist.set_index("date")["close"]
    return pd.DataFrame(cols).sort_index() if cols else pd.DataFrame()


def run(
    session: Session,
    *,
    settings: Settings | None = None,
    source: YFinancePriceSource | None = None,
    period: str = "1y",
) -> None:
    """Compute + store one sector-correlation snapshot (11 SPDRs)."""
    settings = settings or get_settings()  # noqa: F841 — kept for signature parity with other jobs
    source = source or YFinancePriceSource()
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="sector_corr")

    prices = _close_frame(source, SECTOR_SPDRS, period=period)
    if prices.empty:
        bound.warning("sector_corr.no_prices")
        return

    snap = latest_snapshot(prices)
    if snap["avg_corr"].get("21d") is None and snap["avg_corr"].get("63d") is None:
        bound.warning("sector_corr.insufficient_history", rows=int(prices.shape[0]))
        return

    try:
        as_of = date.fromisoformat(str(snap["as_of"]))
    except (TypeError, ValueError):
        bound.warning("sector_corr.bad_as_of", as_of=snap.get("as_of"))
        return

    record = {
        "as_of": as_of,
        "avg_corr_21": snap["avg_corr"].get("21d"),
        "avg_corr_63": snap["avg_corr"].get("63d"),
        "dispersion": snap["dispersion"],
        "n_etfs": snap["n_etfs"],
        "matrix": snap["matrix"],
        "computed_at": eastern_now().replace(microsecond=0),
        "source": _SOURCE,
    }
    stmt = pg_insert(SectorCorrSnapshot).values(record).on_conflict_do_nothing(
        index_elements=["as_of", "source"]
    )
    session.execute(stmt)
    session.commit()
    bound.info(
        "sector_corr.done",
        as_of=str(as_of),
        avg_corr_21=record["avg_corr_21"],
        avg_corr_63=record["avg_corr_63"],
        dispersion=record["dispersion"],
    )


def main() -> None:
    """Manual/scheduled entrypoint."""
    from trading_intel.memory.db import make_session_factory

    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    settings = get_settings()
    with make_session_factory(settings)() as session:
        run(session, settings=settings)


if __name__ == "__main__":
    main()
