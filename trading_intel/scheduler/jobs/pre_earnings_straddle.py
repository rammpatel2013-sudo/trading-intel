"""Scheduled job (daily): snapshot the pre-earnings straddle -> pre_earnings_straddle.

Captures the options-implied expected move for each watchlist name with an
earnings date within ``PRE_EARNINGS_SNAP_DAYS`` — the BASELINE the EM-break
detector measures the realized gap against. Idempotent upsert (rule 5). Descriptor
input only (rule 4).

Manual run:
    python -m trading_intel.scheduler.jobs.pre_earnings_straddle
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_intel.clients import OptionsDataSource
from trading_intel.config import Settings, get_settings
from trading_intel.errors import ComputationError, TradingIntelError
from trading_intel.greeks.black_scholes import years_to_expiry
from trading_intel.greeks.straddle import atm_straddle
from trading_intel.memory.models import EarningsEvent, PreEarningsStraddle
from trading_intel.timeutils import eastern_now

log = structlog.get_logger(__name__)

_UPDATE_COLS = ("snap_ts", "dte", "straddle", "em_pct", "atm_iv", "spot", "source")


def _targets(session: Session, settings: Settings, as_of: date) -> list[tuple[str, date]]:
    """Watchlist names with an earnings date within the snapshot window."""
    horizon = as_of + timedelta(days=settings.PRE_EARNINGS_SNAP_DAYS)
    watch = set(settings.watchlist_symbols)
    rows = session.execute(
        select(EarningsEvent.symbol, EarningsEvent.date)
        .where(EarningsEvent.date >= as_of, EarningsEvent.date <= horizon)
        .order_by(EarningsEvent.date.asc())
    ).all()
    return [(r.symbol, r.date) for r in rows if r.symbol in watch]


def _choose_expiry(chain, as_of: date, earnings_dte: int, target_dte: int):
    """The expiry bracketing earnings and closest to ``target_dte`` (else nearest).

    ``years_to_expiry`` takes the expiration SERIES + a positional ref date (it
    dispatches on the column dtype), so compute DTE for all expirations at once.
    """
    exps = chain["expiration"].dropna().drop_duplicates()
    if exps.empty:
        return None
    dtes = (years_to_expiry(exps, as_of) * 365.0).round().astype(int)
    scored = list(zip(dtes.tolist(), exps.tolist()))
    bracket = [(abs(d - target_dte), e) for d, e in scored if d >= max(0, earnings_dte)]
    pool = bracket or [(abs(d - target_dte), e) for d, e in scored]
    if not pool:
        return None
    return min(pool, key=lambda t: t[0])[1]


def snapshot_one(
    source: OptionsDataSource, symbol: str, edate: date, settings: Settings, as_of: date
) -> dict | None:
    """Price the pre-earnings ATM straddle for one name (None if unpriceable)."""
    chain = source.chain(symbol, exps=tuple(range(1, 7)), strike_range=0.15)
    if chain is None or chain.empty:
        return None
    spot = source.spot(symbol)
    if not spot or spot <= 0:
        return None
    earnings_dte = (edate - as_of).days
    chosen = _choose_expiry(chain, as_of, earnings_dte, settings.PRE_EARNINGS_TARGET_DTE)
    if chosen is None:
        return None
    sub = chain[chain["expiration"] == chosen]
    res = atm_straddle(sub, float(spot), ref_date=as_of)
    straddle = res.get("straddle") if res else None
    if not straddle:
        return None
    spot_f = float(res.get("spot") or spot)
    em_pct = float(straddle) / spot_f if spot_f > 0 else None
    return {
        "symbol": symbol,
        "earnings_date": edate,
        "snap_ts": eastern_now(),
        "dte": res.get("dte"),
        "straddle": float(straddle),
        "em_pct": em_pct,
        "atm_iv": res.get("atm_iv"),
        "spot": spot_f,
        "source": "convex",
    }


def _upsert(session: Session, rows: list[dict]) -> None:
    if not rows:
        return
    stmt = pg_insert(PreEarningsStraddle).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["symbol", "earnings_date"],
        set_={c: stmt.excluded[c] for c in _UPDATE_COLS},
    )
    session.execute(stmt)


def run(session: Session, source: OptionsDataSource, *, settings: Settings | None = None) -> None:
    """Snapshot pre-earnings straddles for names with an upcoming print."""
    settings = settings or get_settings()
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="pre_earnings_straddle")
    as_of = eastern_now().date()
    targets = _targets(session, settings, as_of)
    rows: list[dict] = []
    for sym, edate in targets:
        try:
            snap = snapshot_one(source, sym, edate, settings, as_of)
        except (TradingIntelError, ComputationError) as exc:
            bound.warning("pre_earnings_straddle.skip", symbol=sym, err=str(exc))
            continue
        if snap is not None:
            rows.append(snap)
    _upsert(session, rows)
    session.commit()
    bound.info("pre_earnings_straddle.done", targets=len(targets), rows=len(rows))


def main() -> None:
    """Manual entrypoint: wire Settings -> ConvexClient -> session, run once."""
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
