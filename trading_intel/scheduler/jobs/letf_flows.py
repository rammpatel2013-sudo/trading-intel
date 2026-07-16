"""Scheduled job (EOD): snapshot LETF shares outstanding -> letf_shares_snapshots.

The ingest primitive for LETF net creation/redemption (issuance) flow. FMP's
stable tier serves only the CURRENT shares figure, so this job snapshots every
configured leveraged/inverse ETF once per day and banks the series forward; the
descriptor layer computes Δshares, net issuance $ (= Δshares × price), issuer
buckets, and the k(k-1)·assets·return forced-rebalance estimate from the banked
history.

Idempotent: ``INSERT … ON CONFLICT (symbol, ts) DO UPDATE`` (CLAUDE.md rule 5) —
safe to re-run intraday (it refreshes the day's figure). Regime descriptor only
(FlashAlpha rule 4): issuance is banked like GEX/DEX and emits no signals.

Manual run:
    python -m trading_intel.scheduler.jobs.letf_flows
"""

from __future__ import annotations

import uuid
from datetime import date

import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_intel.clients import EtfFlowSource
from trading_intel.config import Settings, get_settings
from trading_intel.memory.models import LetfSharesSnapshot, Ticker
from trading_intel.timeutils import eastern_now

log = structlog.get_logger(__name__)

_UPDATE_COLS = ("shares_outstanding", "float_shares", "nav", "vendor_date", "source")


def build_rows(
    source: EtfFlowSource,
    settings: Settings,
    *,
    as_of: date | None = None,
    symbols: list[str] | None = None,
) -> list[dict]:
    """Pull one shares-outstanding snapshot per LETF (no upsert)."""
    as_of = as_of or eastern_now().date()
    symbols = symbols or settings.letf_symbols

    rows: list[dict] = []
    for symbol in symbols:
        snap = source.shares_outstanding(symbol)
        if snap is None:
            log.warning("letf_flows.no_shares", symbol=symbol)
            continue
        rows.append(
            {
                "symbol": snap.symbol,
                "ts": as_of,
                "shares_outstanding": snap.shares_outstanding,
                "float_shares": snap.float_shares,
                "nav": None,  # joined downstream from the price layer (quotes_daily.close)
                "vendor_date": snap.as_of,
                "source": snap.source,
            }
        )
    return rows


def _ensure_tickers(session: Session, symbols: set[str]) -> None:
    """Idempotently seed ``tickers`` rows so the FK on the snapshot holds.

    LETFs are not in the single-name watchlist; insert a bare row (symbol only)
    for any we are about to reference. ``ON CONFLICT DO NOTHING`` keeps it safe to
    re-run and never clobbers an existing, richer ticker row.
    """
    if not symbols:
        return
    stmt = pg_insert(Ticker).values([{"symbol": s} for s in sorted(symbols)])
    stmt = stmt.on_conflict_do_nothing(index_elements=["symbol"])
    session.execute(stmt)


def _upsert(session: Session, rows: list[dict]) -> None:
    """Idempotent upsert into ``letf_shares_snapshots`` (refresh on (symbol, ts))."""
    if not rows:
        return
    stmt = pg_insert(LetfSharesSnapshot).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["symbol", "ts"],
        set_={c: stmt.excluded[c] for c in _UPDATE_COLS},
    )
    session.execute(stmt)


def run(
    session: Session,
    source: EtfFlowSource,
    *,
    settings: Settings | None = None,
    symbols: list[str] | None = None,
) -> None:
    """Snapshot today's LETF shares outstanding and upsert them."""
    settings = settings or get_settings()
    correlation_id = uuid.uuid4().hex
    bound = log.bind(correlation_id=correlation_id, job="letf_flows")

    as_of = eastern_now().date()
    rows = build_rows(source, settings, as_of=as_of, symbols=symbols)
    _ensure_tickers(session, {r["symbol"] for r in rows})
    _upsert(session, rows)
    session.commit()

    bound.info("letf_flows.done", as_of=as_of.isoformat(), rows=len(rows))


def main() -> None:
    """Manual entrypoint: wire Settings -> session -> FmpClient, run once."""
    from trading_intel.clients.fmp import FmpClient
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
    source = FmpClient(settings)
    session_factory = make_session_factory(settings)
    with session_factory() as session:
        run(session, source, settings=settings)


if __name__ == "__main__":
    main()
