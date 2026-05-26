"""Scheduled job (5-min): intraday all-expiry traded delta-notional flow.

For the focused intraday symbols, pulls the wide all-expiry chain every 5 minutes
and records the running dollar-delta of the day's option flow — call vs put,
summed over ALL expiries and over the NEXT (nearest) expiry — into ``delta_flow``.
This powers the delta-notional flow chart (price overlaid with cumulative call/put
delta). Reads ``volume`` (cumulative session volume) so each snapshot is already
the running cumulative line.

Idempotent: ``INSERT ... ON CONFLICT (symbol, ts, source) DO UPDATE`` with ``ts``
floored to the 5-minute slot, so a re-run in the same slot refreshes rather than
duplicates (CLAUDE.md rule 5). Self-guards to regular trading hours. Descriptor
only — emits no signals (FlashAlpha rule 4).

Manual run:
    python -m trading_intel.scheduler.jobs.delta_flow
"""
from __future__ import annotations

import uuid
from datetime import datetime

import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_intel.clients import OptionsDataSource
from trading_intel.config import Settings, get_settings
from trading_intel.errors import TradingIntelError
from trading_intel.greeks.delta_flow import delta_notional_split
from trading_intel.greeks.intraday_flow import is_market_hours
from trading_intel.memory.models import DeltaFlow
from trading_intel.timeutils import eastern_now

log = structlog.get_logger(__name__)

_SLOT_MINUTES = 5
_SOURCE = "convex"
_UQ_COLS = ["symbol", "ts", "source"]
_UPDATE_COLS = (
    "spot", "next_expiry", "call_notional_all", "put_notional_all",
    "call_notional_next", "put_notional_next",
)


def _floor_to_slot(now: datetime, minutes: int = _SLOT_MINUTES) -> datetime:
    return now.replace(minute=(now.minute // minutes) * minutes, second=0, microsecond=0)


def build_record(symbol: str, ts: datetime, chain: object, spot: float | None) -> dict | None:
    """Compute one ``delta_flow`` row from a wide chain + spot (None if unusable)."""
    split = delta_notional_split(chain, spot)  # type: ignore[arg-type]
    if split is None:
        return None
    return {
        "symbol": symbol,
        "ts": ts,
        "source": _SOURCE,
        "spot": float(spot) if spot is not None else None,
        "next_expiry": split.next_expiry,
        "call_notional_all": split.call_notional_all,
        "put_notional_all": split.put_notional_all,
        "call_notional_next": split.call_notional_next,
        "put_notional_next": split.put_notional_next,
    }


def run(
    session: Session,
    source: OptionsDataSource,
    *,
    settings: Settings | None = None,
    force: bool = False,
) -> None:
    """Snapshot the focused symbols' all-expiry delta-notional flow into ``delta_flow``."""
    settings = settings or get_settings()
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="delta_flow")

    now = eastern_now()
    if not force and not is_market_hours(now):
        bound.info("delta_flow.skipped_off_hours", now=now.isoformat())
        return

    ts = _floor_to_slot(now)
    symbols = settings.intraday_symbols
    bound.info("delta_flow.start", ts=ts.isoformat(), symbols=symbols)

    records: list[dict] = []
    failed = 0
    for symbol in symbols:
        try:
            chain = source.chain_long(symbol)  # type: ignore[attr-defined]
            spot = source.spot(symbol)
        except (TradingIntelError, AttributeError) as exc:
            failed += 1
            bound.warning("delta_flow.symbol_failed", symbol=symbol, error=str(exc))
            continue
        record = build_record(symbol, ts, chain, spot)
        if record is None:
            bound.warning("delta_flow.empty", symbol=symbol)
            continue
        records.append(record)

    if records:
        stmt = pg_insert(DeltaFlow).values(records)
        stmt = stmt.on_conflict_do_update(
            index_elements=_UQ_COLS,
            set_={c: stmt.excluded[c] for c in _UPDATE_COLS},
        )
        session.execute(stmt)
    session.commit()
    bound.info("delta_flow.done", rows=len(records), failed=failed)


def main() -> None:
    """Manual entrypoint: wire Settings -> session -> ConvexClient, run once."""
    from trading_intel.clients.convex import ConvexClient
    from trading_intel.memory.db import make_session_factory

    settings = get_settings()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    source = ConvexClient(settings)
    session_factory = make_session_factory(settings)
    with session_factory() as session:
        run(session, source, settings=settings, force=True)


if __name__ == "__main__":
    main()
