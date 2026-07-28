"""Scheduled job (weekly): analyst EPS/revenue estimates -> ``estimate_snapshots``.

Pulls the nearest upcoming fiscal period's consensus EPS/revenue estimate per
name from CVForge FMP (ADR-005 — no new vendor) and banks one row per
(symbol, ts). Banked forward so the earnings-alignment screen can read the
*revision* (this week's estimate vs a prior week's) — the top-quality of its
three signals. Idempotent weekly upsert on (symbol, ts) (CLAUDE.md rule 5);
descriptor only (FlashAlpha rule 4).

FMP spells estimate fields several ways and returns a list-of-periods; the
extractor is tolerant + degrades to None (same posture as ``sentiment.fmp_map``).
Confirm the endpoint name + field spellings against the live payload on first run.

Manual run:
    python -m trading_intel.scheduler.jobs.estimate_snapshots
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from datetime import date
from typing import Any, TypeVar

import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_intel.clients.cvforge import CVForgeClient
from trading_intel.clients.fmp import FmpClient
from trading_intel.config import Settings, get_settings
from trading_intel.errors import DataSourceError
from trading_intel.memory.models import EstimateSnapshot, Ticker
from trading_intel.timeutils import eastern_now

log = structlog.get_logger(__name__)

_T = TypeVar("_T")
_UPDATE = ("period_date", "eps_avg", "eps_high", "eps_low", "eps_num", "revenue_avg", "source")

_EPS_AVG = ("estimatedEpsAvg", "epsAvg", "estimatedEpsAvgAnalyst")
_EPS_HIGH = ("estimatedEpsHigh", "epsHigh")
_EPS_LOW = ("estimatedEpsLow", "epsLow")
_EPS_NUM = (
    "numAnalystsEps",
    "numberAnalystEstimatedEps",
    "numberAnalystsEstimatedEps",
    "numberAnalysts",
)
_REV_AVG = ("estimatedRevenueAvg", "revenueAvg")
_DATE = ("date", "period", "fiscalDate")

# FMP's stable ``analyst-estimates`` route REQUIRES ``period`` (annual|quarter);
# omitting it 502s through the CVForge gateway. ``limit`` gives ``_pick_period``
# a few fiscal years to choose the nearest-future estimate from. Confirmed
# working via ``research/enrich.py::pull_analyst`` (period="annual").
_ESTIMATE_PARAMS = {"period": "annual", "limit": 8}


def _safe(fn: Callable[[], _T]) -> _T | None:
    try:
        return fn()
    except DataSourceError:
        return None


def _fetch_estimates(
    client: CVForgeClient,
    fmp: FmpClient | None,
    symbol: str,
    *,
    retries: int = 1,
    pause: float = 0.5,
) -> object | None:
    """Nearest-period analyst estimates for one symbol, resiliently.

    Primary is the CVForge FMP passthrough (paid tier, ADR-005) — the same route
    ``research/enrich.py`` uses. That tier 502s sporadically on the FMP endpoints
    (see enrich.py docstring), so a transient gateway failure is retried once.
    Falls back to the direct stable FMP client (``settings.FMP_API``) when the
    passthrough still yields nothing. Read-only; descriptive (rule 4)."""
    params = {"symbol": symbol, **_ESTIMATE_PARAMS}
    for attempt in range(retries + 1):
        payload = _safe(lambda p=params: client.fmp("analyst-estimates", p))
        if payload:
            return payload
        if attempt < retries:
            time.sleep(pause)
    if fmp is not None:
        direct = fmp.analyst_estimates(symbol)
        if direct:
            return direct
    return None


def _num(rec: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for k in keys:
        v = rec.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


def _pick_period(payload: object, today: date) -> tuple[date | None, dict[str, Any] | None]:
    """From an FMP analyst-estimates payload, the nearest period on/after today."""
    records = (
        payload if isinstance(payload, list) else [payload] if isinstance(payload, dict) else []
    )
    dated: list[tuple[date | None, dict[str, Any]]] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        d: date | None = None
        for k in _DATE:
            raw = rec.get(k)
            if raw:
                try:
                    d = date.fromisoformat(str(raw)[:10])
                except ValueError:
                    d = None
                break
        dated.append((d, rec))
    future = [(d, r) for d, r in dated if d and d >= today]
    if future:
        return min(future, key=lambda t: t[0])
    return dated[0] if dated else (None, None)


def _extract(symbol: str, payload: object, *, as_of: date) -> dict[str, Any] | None:
    period_date, rec = _pick_period(payload, as_of)
    if rec is None:
        return None
    return {
        "symbol": symbol[:16],
        "ts": as_of,
        "period_date": period_date,
        "eps_avg": _num(rec, _EPS_AVG),
        "eps_high": _num(rec, _EPS_HIGH),
        "eps_low": _num(rec, _EPS_LOW),
        "eps_num": _num(rec, _EPS_NUM),
        "revenue_avg": _num(rec, _REV_AVG),
        "source": "cvforge-fmp",
    }


def run(
    session: Session,
    client: CVForgeClient,
    *,
    settings: Settings | None = None,
    symbols: list[str] | None = None,
    fmp: FmpClient | None = None,
) -> int:
    """Snapshot this week's analyst EPS/revenue estimates for the universe."""
    settings = settings or get_settings()
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="estimate_snapshots")
    # Bounded universe (widen via SENTIMENT_UNIVERSE if needed).
    syms = symbols or settings.sentiment_symbols
    as_of = eastern_now().date()

    rows: list[dict[str, Any]] = []
    for sym in syms:
        payload = _fetch_estimates(client, fmp, sym)
        if payload is None:
            continue
        row = _extract(sym, payload, as_of=as_of)
        if row and (row["eps_avg"] is not None or row["revenue_avg"] is not None):
            rows.append(row)

    if rows:
        session.execute(
            pg_insert(Ticker)
            .values([{"symbol": r["symbol"]} for r in rows])
            .on_conflict_do_nothing(index_elements=["symbol"])
        )
        stmt = pg_insert(EstimateSnapshot).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["symbol", "ts"],
            set_={c: stmt.excluded[c] for c in _UPDATE},
        )
        session.execute(stmt)
    session.commit()
    bound.info("estimate_snapshots.done", as_of=as_of.isoformat(), rows=len(rows))
    return len(rows)


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
    client = CVForgeClient(settings)
    fmp = FmpClient(settings)
    session_factory = make_session_factory(settings)
    try:
        with session_factory() as session:
            run(session, client, settings=settings, fmp=fmp)
    finally:
        client.close()


if __name__ == "__main__":
    main()
