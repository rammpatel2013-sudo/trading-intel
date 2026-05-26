"""Scheduled job: snapshot aggregate options flow for the watchlist.

For each watchlist symbol it pulls the per-strike flow chain (premium notional)
and the per-trade time & sales, then stores one ``flow_snapshots`` row with the
call/put notional, put/call tilt, net premium, the largest prints
(``top_prints``) and notable multi-leg packages (``packages``). Both vendor
pulls are best-effort — a symbol that errors or is pre-open is skipped, not
fatal. Idempotent: ``ON CONFLICT (symbol, ts, source) DO NOTHING`` with ``ts``
floored to the minute (CLAUDE.md rule 5).

Data collection only — emits no signals/alerts (FlashAlpha rule 4). The flow
math lives in ``strategies/options_flow.py`` (descriptive aggregator).

Manual run (ignores the market-hours guard):
    python -m trading_intel.scheduler.jobs.flow_snapshot
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
from trading_intel.greeks.intraday_flow import is_market_hours
from trading_intel.memory.models import FlowSnapshot
from trading_intel.strategies.options_flow import (
    FlowSummary,
    Structure,
    aggregate_flow,
    detect_structures,
)
from trading_intel.timeutils import eastern_now
from trading_intel.watchlist import effective_symbols

log = structlog.get_logger(__name__)

_SOURCE = "convex"
_UQ_COLS = ["symbol", "ts", "source"]


def _str_date(value: object) -> str | None:
    try:
        ts = pd.Timestamp(value)
    except (ValueError, TypeError):
        return str(value) if value is not None else None
    return ts.strftime("%Y-%m-%d") if pd.notna(ts) else None


def _f(value: object) -> float | None:
    num = pd.to_numeric(value, errors="coerce")
    return float(num) if pd.notna(num) else None


def _json_prints(summary: FlowSummary) -> list[dict]:
    """JSON-safe largest-print records."""
    out: list[dict] = []
    for pr in summary.top_prints:
        out.append(
            {
                "expiration": _str_date(pr.get("expiration")),
                "strike": _f(pr.get("strike")),
                "opt_kind": str(pr.get("opt_kind")) if pr.get("opt_kind") is not None else None,
                "premium": _f(pr.get("premium")),
                "iv": _f(pr.get("iv")),
            }
        )
    return out


def _json_packages(structures: list[Structure], *, top_n: int) -> list[dict]:
    """JSON-safe notable multi-leg packages (n_legs > 1), largest first."""
    out: list[dict] = []
    for st in structures:
        if st.n_legs <= 1:
            continue
        out.append(
            {
                "time": _str_time(st.time),
                "root": st.root,
                "kind": st.kind,
                "n_legs": int(st.n_legs),
                "total_premium": _f(st.total_premium),
                "net_premium": _f(st.net_premium),
                "expirations": list(st.expirations),
                "legs": [
                    {
                        "expiration": _str_date(leg.get("expiration")),
                        "strike": _f(leg.get("strike")),
                        "opt_kind": str(leg.get("opt_kind"))
                        if leg.get("opt_kind") is not None
                        else None,
                        "premium": _f(leg.get("premium")),
                    }
                    for leg in st.legs
                ],
            }
        )
        if len(out) >= top_n:
            break
    return out


def _str_time(value: object) -> str | None:
    try:
        ts = pd.Timestamp(value)
    except (ValueError, TypeError):
        return str(value) if value is not None else None
    return ts.isoformat() if pd.notna(ts) else None


def _build_record(
    symbol: str, ts: datetime, *, settings: Settings, source: OptionsDataSource
) -> dict | None:
    """Pull flow + tas for one symbol and build a flow_snapshots row (or None)."""
    try:
        summary = aggregate_flow(source.flow_chain(symbol), top_n=settings.FLOW_TOP_N)
    except TradingIntelError:
        return None

    packages: list[dict] = []
    try:
        structures = detect_structures(
            source.time_and_sales(symbol), min_premium=settings.FLOW_MIN_PACKAGE_PREMIUM
        )
        packages = _json_packages(structures, top_n=settings.FLOW_TOP_N)
    except TradingIntelError:
        packages = []

    return {
        "symbol": symbol,
        "ts": ts,
        "source": _SOURCE,
        "call_notional": _f(summary.call_notional),
        "put_notional": _f(summary.put_notional),
        "net_premium": _f(summary.net_premium),
        "put_call_ratio": _f(summary.put_call_ratio),
        "tilt": summary.tilt,
        "n_prints": int(summary.n_prints),
        "top_prints": _json_prints(summary),
        "packages": packages,
    }


def run(
    session: Session,
    source: OptionsDataSource,
    *,
    settings: Settings | None = None,
    force: bool = False,
) -> None:
    """Snapshot watchlist options flow into ``flow_snapshots``."""
    settings = settings or get_settings()
    correlation_id = uuid.uuid4().hex
    bound = log.bind(correlation_id=correlation_id, job="flow_snapshot")

    now = eastern_now()
    if not force and not is_market_hours(now):
        bound.info("flow_snapshot.skipped_off_hours", now=now.isoformat())
        return

    ts = now.replace(second=0, microsecond=0)
    symbols = effective_symbols(session, settings)
    bound.info("flow_snapshot.start", ts=ts.isoformat(), symbol_count=len(symbols))

    written = 0
    skipped = 0
    for symbol in symbols:
        record = _build_record(symbol, ts, settings=settings, source=source)
        if record is None:
            skipped += 1
            bound.warning("flow_snapshot.empty", symbol=symbol)
            continue
        stmt = pg_insert(FlowSnapshot).values(record).on_conflict_do_nothing(
            index_elements=_UQ_COLS
        )
        session.execute(stmt)
        written += 1
        bound.debug("flow_snapshot.symbol", symbol=symbol, n_prints=record["n_prints"])

    session.commit()
    bound.info("flow_snapshot.done", written=written, skipped=skipped)


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
