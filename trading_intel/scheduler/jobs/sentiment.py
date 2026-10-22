"""Scheduled job (weekly): institutional + analyst sentiment -> ``sentiment_snapshots``.

Pulls FMP institutional-ownership (latest 13F quarter), price-target-consensus,
grades-consensus and a quote for the sentiment universe via CVForge (ADR-005 — no new
vendor), maps them to ``SentimentInputs`` (``sentiment.fmp_map``), adds the pure
derivations (implied upside, Buy-share) and banks one row per (symbol, ts).

Idempotent weekly upsert on (symbol, ts) (CLAUDE.md rule 5). Institutional 13F is
quarterly + lagged; analyst targets move weekly — so the *trend* of these rows
(target-cut rate, institutional accumulation) is the descriptor, never a standalone
signal (FlashAlpha rule 4).

Manual run:
    python -m trading_intel.scheduler.jobs.sentiment
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from datetime import date
from typing import TypeVar

import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_intel.clients.cvforge import CVForgeClient
from trading_intel.config import Settings, get_settings
from trading_intel.errors import DataSourceError
from trading_intel.memory.models import SentimentSnapshot, Ticker
from trading_intel.sentiment import (
    DERIVED_FIELDS,
    RAW_FIELDS,
    SentimentInputs,
    derived_fields,
    extract_inputs,
)
from trading_intel.timeutils import eastern_now

log = structlog.get_logger(__name__)

_UPDATE_COLS = (*RAW_FIELDS, *DERIVED_FIELDS, "rating_consensus", "source")

_T = TypeVar("_T")


def _safe(fn: Callable[[], _T]) -> _T | None:
    """Run ``fn``; a transient CVForge ``DataSourceError`` (e.g. a 502) -> None."""
    try:
        return fn()
    except DataSourceError:
        return None


def latest_13f_quarter(as_of: date, *, lag_days: int = 50) -> tuple[int, int]:
    """(year, quarter) of the newest 13F quarter fully filed by ``as_of``.

    13Fs are due 45 days after quarter end; ``lag_days`` adds a small buffer.
    """
    from datetime import timedelta

    d = as_of - timedelta(days=lag_days)
    q = (d.month - 1) // 3  # quarter BEFORE the one d sits in (0 = previous year's Q4)
    return (d.year, q) if q else (d.year - 1, 4)


def fetch_inputs(client: object, symbol: str, *, as_of: date | None = None) -> SentimentInputs:
    """Pull the FMP institutional + analyst payloads for one name (best-effort).

    Works with the direct ``FmpClient`` (paid tier, preferred) or ``CVForgeClient``.
    Institutional: the stable ``symbol-positions-summary`` (needs year/quarter) first,
    then the older ``symbol-ownership`` spelling.
    """
    yr, qtr = latest_13f_quarter(as_of or eastern_now().date())
    inst = _safe(lambda: client.fmp(
        "institutional-ownership/symbol-positions-summary",
        {"symbol": symbol, "year": yr, "quarter": qtr}))
    if not inst:
        inst = _safe(
            lambda: client.fmp("institutional-ownership/symbol-ownership", {"symbol": symbol})
        )
    targets = _safe(lambda: client.fmp("price-target-consensus", {"symbol": symbol}))
    grades = _safe(lambda: client.fmp("grades-consensus", {"symbol": symbol}))
    quote = _safe(lambda: client.fmp("quote", {"symbol": symbol}))
    return extract_inputs(symbol, inst=inst, targets=targets, grades=grades, quote=quote)


def build_rows(client: object, symbols: Sequence[str], *, as_of: date,
               source: str = "cvforge-fmp") -> list[dict]:
    """Fetch inputs for the universe, add derivations, build row dicts."""
    rows: list[dict] = []
    for sym in symbols:
        inp = fetch_inputs(client, sym, as_of=as_of)
        row: dict = {"symbol": inp.symbol, "ts": as_of, "source": source}
        row.update({c: getattr(inp, c) for c in RAW_FIELDS})
        row["rating_consensus"] = inp.rating_consensus
        row.update(derived_fields(inp))
        rows.append(row)
    return rows


def _ensure_tickers(session: Session, symbols: set[str]) -> None:
    if not symbols:
        return
    stmt = pg_insert(Ticker).values([{"symbol": s} for s in sorted(symbols)])
    session.execute(stmt.on_conflict_do_nothing(index_elements=["symbol"]))


def _upsert(session: Session, rows: list[dict]) -> None:
    if not rows:
        return
    stmt = pg_insert(SentimentSnapshot).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["symbol", "ts"],
        set_={c: stmt.excluded[c] for c in _UPDATE_COLS},
    )
    session.execute(stmt)


def run(
    session: Session,
    client: object,
    *,
    settings: Settings | None = None,
    symbols: list[str] | None = None,
) -> int:
    """Snapshot this week's institutional + analyst sentiment for the universe."""
    settings = settings or get_settings()
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="sentiment")
    syms = symbols or settings.sentiment_symbols
    as_of = eastern_now().date()
    src = "fmp" if type(client).__name__ == "FmpClient" else "cvforge-fmp"
    rows = build_rows(client, syms, as_of=as_of, source=src)
    # All-null rows mean the endpoints are still gated — don't bank empty history.
    rows = [r for r in rows if any(r.get(c) is not None for c in RAW_FIELDS)]
    _ensure_tickers(session, {r["symbol"] for r in rows})
    _upsert(session, rows)
    session.commit()
    bound.info("sentiment.done", as_of=as_of.isoformat(), rows=len(rows))
    return len(rows)


def make_client(settings: Settings) -> object:
    """Direct FMP (paid tier) when ``FMP_API`` is set, else the CVForge passthrough."""
    key = settings.FMP_API.get_secret_value() if settings.FMP_API else ""
    if key:
        from trading_intel.clients.fmp import FmpClient

        return FmpClient(settings)
    return CVForgeClient(settings)


def main() -> None:
    """Manual/NAS entrypoint: wire Settings -> session -> CVForge, run once."""
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
    client = make_client(settings)
    session_factory = make_session_factory(settings)
    try:
        with session_factory() as session:
            run(session, client, settings=settings)
    finally:
        client.close()


if __name__ == "__main__":
    main()
