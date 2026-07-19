"""Scheduled job (weekly): fundamentals + factor scores -> ``fundamentals_snapshots``.

Pulls FMP fundamentals (ratios-ttm / key-metrics-ttm / financial-growth / profile)
and ~400d of daily closes for the factor universe via CVForge (ADR-004/005 — no
new vendor), maps them to ``FactorInputs`` (``factors.fmp_map``), computes the
cross-sectional Value/Quality/Growth/Momentum/Risk z-scores + composite
(``factors.compute``), and banks one row per (symbol, week).

Cross-sectional scores are relative to the universe, so the whole batch is scored
together. Idempotent weekly upsert on (symbol, ts) (CLAUDE.md rule 5). Descriptive
research scores only — never a standalone signal (FlashAlpha rule 4).

Manual run:
    python -m trading_intel.scheduler.jobs.factor_scores
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from datetime import date, timedelta
from typing import TypeVar

import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_intel.clients.cvforge import CVForgeClient
from trading_intel.config import Settings, get_settings
from trading_intel.errors import DataSourceError
from trading_intel.factors import FactorInputs, compute_factor_scores
from trading_intel.factors.fmp_map import extract_inputs
from trading_intel.memory.models import FundamentalsSnapshot, Ticker
from trading_intel.timeutils import eastern_now

log = structlog.get_logger(__name__)

_RAW_COLS = (
    "pe",
    "pb",
    "ps",
    "ev_ebitda",
    "roe",
    "roic",
    "gross_margin",
    "net_margin",
    "fcf_margin",
    "debt_to_equity",
    "current_ratio",
    "revenue_growth",
    "eps_growth",
    "beta",
    "ret_3m",
    "ret_12m",
)
_SCORE_MAP = {
    "value_score": "value",
    "quality_score": "quality",
    "growth_score": "growth",
    "momentum_score": "momentum",
    "risk_score": "risk",
    "composite_score": "composite",
}
_UPDATE_COLS = (*_RAW_COLS, *_SCORE_MAP, "source")

_T = TypeVar("_T")


def _safe(fn: Callable[[], _T]) -> _T | None:
    """Run ``fn``; a transient CVForge ``DataSourceError`` (e.g. a 502) -> None."""
    try:
        return fn()
    except DataSourceError:
        return None


def fetch_inputs(client: CVForgeClient, symbol: str) -> FactorInputs:
    """Pull the FMP fundamentals + momentum closes for one name (best-effort)."""
    profile = _safe(lambda: client.fmp("profile", {"symbol": symbol}))
    ratios = _safe(lambda: client.fmp("ratios-ttm", {"symbol": symbol}))
    key_metrics = _safe(lambda: client.fmp("key-metrics-ttm", {"symbol": symbol}))
    growth = _safe(lambda: client.fmp("financial-growth", {"symbol": symbol}))
    frm = (date.today() - timedelta(days=400)).isoformat()
    bars = _safe(lambda: client.aggs(symbol, frm=frm, to=date.today().isoformat()))
    closes = bars["c"].to_numpy(dtype=float) if (bars is not None and not bars.empty) else None
    return extract_inputs(
        symbol,
        profile=profile,
        ratios=ratios,
        key_metrics=key_metrics,
        growth=growth,
        closes=closes,
    )


def build_rows(client: CVForgeClient, symbols: Sequence[str], *, as_of: date) -> list[dict]:
    """Fetch inputs for the universe, compute cross-sectional scores, build row dicts."""
    inputs = [fetch_inputs(client, s) for s in symbols]
    scored = {s.symbol: s for s in compute_factor_scores(inputs)}
    rows: list[dict] = []
    for inp in inputs:
        sc = scored[inp.symbol]
        row: dict = {"symbol": inp.symbol, "ts": as_of, "source": "cvforge-fmp"}
        row.update({c: getattr(inp, c) for c in _RAW_COLS})
        row.update({col: getattr(sc, attr) for col, attr in _SCORE_MAP.items()})
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
    stmt = pg_insert(FundamentalsSnapshot).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["symbol", "ts"],
        set_={c: stmt.excluded[c] for c in _UPDATE_COLS},
    )
    session.execute(stmt)


def run(
    session: Session,
    client: CVForgeClient,
    *,
    settings: Settings | None = None,
    symbols: list[str] | None = None,
) -> int:
    """Snapshot this week's fundamentals + factor scores for the universe."""
    settings = settings or get_settings()
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="factor_scores")
    syms = symbols or settings.factor_symbols
    as_of = eastern_now().date()
    rows = build_rows(client, syms, as_of=as_of)
    _ensure_tickers(session, {r["symbol"] for r in rows})
    _upsert(session, rows)
    session.commit()
    bound.info("factor_scores.done", as_of=as_of.isoformat(), rows=len(rows))
    return len(rows)


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
    client = CVForgeClient(settings)
    session_factory = make_session_factory(settings)
    try:
        with session_factory() as session:
            run(session, client, settings=settings)
    finally:
        client.close()


if __name__ == "__main__":
    main()
