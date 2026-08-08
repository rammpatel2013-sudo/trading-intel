"""Scheduled job (daily): FINRA short data -> ``short_interest_snapshots``.

For each watchlist symbol, banks the daily Reg SHO short-volume ratio (free, no
auth) and — when FINRA API creds are set — the settled bi-monthly short interest
+ days-to-cover. Idempotent upsert on (symbol, ts, source) (rule 5); descriptive
only (rule 4). FINRA is spoken only via ``clients/finra.py`` (rule 1).

Manual run:
    python -m trading_intel.scheduler.jobs.short_interest_snapshots
"""

from __future__ import annotations

import uuid
from datetime import date

import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_intel.clients.finra import FinraClient
from trading_intel.config import Settings, get_settings
from trading_intel.memory.models import ShortInterestSnapshot, Ticker
from trading_intel.timeutils import eastern_now

log = structlog.get_logger(__name__)

_DAILY_UPDATE = ("short_volume", "total_volume", "short_volume_ratio", "short_volume_ratio_avg")
_SI_UPDATE = ("short_interest", "avg_daily_volume", "days_to_cover", "settlement_date")


def _daily_row(finra: FinraClient, sym: str, as_of: date) -> dict | None:
    avg = finra.short_volume_avg(sym, lookback=10, end=as_of)
    if not avg:
        return None
    latest = avg.get("latest") or {}
    return {
        "symbol": sym[:16],
        "ts": as_of,
        "source": "regsho_daily",
        "short_volume": latest.get("short_volume"),
        "total_volume": latest.get("total_volume"),
        "short_volume_ratio": latest.get("short_ratio"),
        "short_volume_ratio_avg": avg.get("short_ratio_avg"),
    }


def _si_row(finra: FinraClient, sym: str, as_of: date) -> dict | None:
    si = finra.settled_short_interest(sym)
    if not si:
        return None
    settle: date | None = None
    sd = si.get("settlement_date")
    if isinstance(sd, str) and len(sd) >= 10:
        try:
            settle = date.fromisoformat(sd[:10])
        except ValueError:
            settle = None
    return {
        "symbol": sym[:16],
        "ts": as_of,
        "source": "finra_si",
        "short_interest": si.get("short_interest"),
        "avg_daily_volume": si.get("avg_daily_volume"),
        "days_to_cover": si.get("days_to_cover"),
        "settlement_date": settle,
    }


def _upsert(session: Session, rows: list[dict], update_cols: tuple[str, ...]) -> None:
    if not rows:
        return
    session.execute(
        pg_insert(Ticker)
        .values([{"symbol": r["symbol"]} for r in rows])
        .on_conflict_do_nothing(index_elements=["symbol"])
    )
    stmt = pg_insert(ShortInterestSnapshot).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["symbol", "ts", "source"],
        set_={c: stmt.excluded[c] for c in update_cols},
    )
    session.execute(stmt)


def run(
    session: Session,
    *,
    settings: Settings | None = None,
    symbols: list[str] | None = None,
    finra: FinraClient | None = None,
    as_of: date | None = None,
) -> int:
    """Bank today's FINRA short data for the universe. Returns rows written."""
    settings = settings or get_settings()
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="short_interest_snapshots")
    as_of = as_of or eastern_now().date()
    syms = symbols or settings.watchlist_symbols
    if finra is None:
        finra = FinraClient(
            client_id=getattr(settings, "FINRA_CLIENT_ID", "") or None,
            client_secret=getattr(settings, "FINRA_CLIENT_SECRET", "") or None,
        )

    daily: list[dict] = []
    settled: list[dict] = []
    for sym in syms:
        d = _daily_row(finra, sym, as_of)
        if d:
            daily.append(d)
        s = _si_row(finra, sym, as_of)
        if s:
            settled.append(s)

    _upsert(session, daily, _DAILY_UPDATE)
    _upsert(session, settled, _SI_UPDATE)
    session.commit()
    bound.info(
        "short_interest_snapshots.done",
        as_of=as_of.isoformat(),
        daily=len(daily),
        settled=len(settled),
    )
    return len(daily) + len(settled)


def main() -> None:
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
    session_factory = make_session_factory(settings)
    with session_factory() as session:
        run(session, settings=settings)


if __name__ == "__main__":
    main()
