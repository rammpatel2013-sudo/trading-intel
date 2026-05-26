"""Scheduled job: snapshot the full per-strike Greeks chain for the watchlist.

Pulls the normalized options chain (delta/gamma/theta/vega/vanna/charm/iv +
oi/volume + gxoi/dxoi/vxoi) for every watchlist symbol from the configured
``OptionsDataSource`` and writes one ``greeks_chain`` row per (expiry, strike,
side). This is the heavier per-strike snapshot that feeds the day-over-day vol
and fixed-strike change panels (Dashboard 1). Idempotent:
``INSERT ... ON CONFLICT (symbol, ts, source, expiry, strike, cp) DO NOTHING``
(CLAUDE.md rule 5), with ``ts`` floored to the minute so re-running the same
scheduled slot does not duplicate rows.

Data collection only — emits no signals/alerts (FlashAlpha rule 4). The feed
exposes no ``cxoi`` (charm-OI exposure), so that column is left null.

Manual run:
    python -m trading_intel.scheduler.jobs.chain_snapshot
"""
from __future__ import annotations

import uuid
from datetime import datetime

import pandas as pd
import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_intel.clients import OptionsDataSource
from trading_intel.config import Settings, get_settings
from trading_intel.errors import TradingIntelError
from trading_intel.memory.models import GreeksChain
from trading_intel.timeutils import eastern_now
from trading_intel.watchlist import effective_symbols

log = structlog.get_logger(__name__)

_SOURCE = "convex"
_UQ_COLS = ["symbol", "ts", "source", "expiry", "strike", "cp"]
_GREEK_COLS = (
    "oi",
    "volume",
    "delta",
    "gamma",
    "theta",
    "vega",
    "vanna",
    "charm",
    "iv",
    "gxoi",
    "dxoi",
    "vxoi",
    "cxoi",
)
_INT_COLS = ("oi", "volume")


def _chain_to_records(
    chain: pd.DataFrame, *, symbol: str, ts: datetime, source: str = _SOURCE
) -> list[dict]:
    """Map a normalized ``OptionsDataSource.chain`` frame to ``greeks_chain`` rows.

    Drops rows missing an expiration/strike or a non call/put side, coerces
    greeks to numbers, and converts NaN -> None so the nullable columns store
    NULL. ``cxoi`` is absent from the feed and stays null.
    """
    needed = {"expiration", "strike", "opt_kind"}
    if chain is None or chain.empty or not needed.issubset(chain.columns):
        return []

    df = chain[chain["expiration"].notna() & chain["strike"].notna()].copy()
    cp = df["opt_kind"].astype(str).str.upper().str[0]
    keep = cp.isin(["C", "P"])
    df, cp = df[keep], cp[keep]
    if df.empty:
        return []

    def col(name: str) -> pd.Series:
        if name in df.columns:
            return pd.to_numeric(df[name], errors="coerce")
        return pd.Series(float("nan"), index=df.index)

    out = pd.DataFrame(
        {
            "symbol": symbol,
            "ts": ts,
            "expiry": pd.to_datetime(df["expiration"]).dt.date,
            "strike": col("strike").astype(float),
            "cp": cp,
            "source": source,
            **{name: col(name) for name in _GREEK_COLS},
        }
    )
    out = out.astype(object).where(pd.notna(out), None)

    records = out.to_dict("records")
    for rec in records:
        for k in _INT_COLS:
            if rec[k] is not None:
                rec[k] = int(rec[k])
    return records


def run(
    session: Session,
    source: OptionsDataSource,
    *,
    settings: Settings | None = None,
    symbols: list[str] | None = None,
) -> None:
    """Snapshot the watchlist per-strike Greeks chain into ``greeks_chain``.

    Args:
        session: an open SQLAlchemy session (committed here).
        source: any ``OptionsDataSource`` implementation.
        settings: optional override; defaults to the process settings.
    """
    settings = settings or get_settings()
    correlation_id = uuid.uuid4().hex
    bound = log.bind(correlation_id=correlation_id, job="chain_snapshot")

    ts = eastern_now().replace(second=0, microsecond=0)
    symbols = symbols or effective_symbols(session, settings)
    bound.info("chain_snapshot.start", ts=ts.isoformat(), symbol_count=len(symbols))

    rows_written = 0
    failed = 0
    for symbol in symbols:
        try:
            chain = source.chain(symbol)
        except TradingIntelError as exc:
            failed += 1
            bound.warning("chain_snapshot.symbol_failed", symbol=symbol, error=str(exc))
            continue

        records = _chain_to_records(chain, symbol=symbol, ts=ts)
        if not records:
            bound.warning("chain_snapshot.empty", symbol=symbol)
            continue

        stmt = pg_insert(GreeksChain).values(records).on_conflict_do_nothing(
            index_elements=_UQ_COLS
        )
        session.execute(stmt)
        rows_written += len(records)
        bound.debug("chain_snapshot.symbol", symbol=symbol, rows=len(records))

    session.commit()
    bound.info("chain_snapshot.done", rows=rows_written, failed=failed)


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
        run(session, source, settings=settings)


if __name__ == "__main__":
    main()
