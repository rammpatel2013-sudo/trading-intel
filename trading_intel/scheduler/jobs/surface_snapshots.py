"""Scheduled job (EOD): near-money per-STRIKE IV surface for index ETFs (fixed-strike).

Banks the OTM-wing IV at each near-money listed strike (|delta| ~0.05..0.95) for the
nearest ``SURFACE_EXPIRIES`` expiries of SPX/QQQ/SPY, straight from a live chain (one
``chain_long`` per root). One row per (symbol, ts, expiry_date, strike) with the option's
``delta`` + ``spot`` stored too. Keyed by STRIKE so day-over-day changes and the vol
footprint track the SAME contract (fixed strike = the receipt; fixed delta gets smeared as
spot slides along the skew). Idempotent upsert on (symbol, ts, expiry_date, strike)
(CLAUDE.md rule 5). Descriptor only (rule 4).

Manual run:
    python -m trading_intel.scheduler.jobs.surface_snapshots
"""

from __future__ import annotations

import uuid
from datetime import date

import pandas as pd
import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_intel.clients import OptionsDataSource
from trading_intel.config import Settings, get_settings
from trading_intel.errors import TradingIntelError
from trading_intel.memory.models import SurfaceSnapshot, Ticker
from trading_intel.timeutils import eastern_now

log = structlog.get_logger(__name__)

_MAX_EXPS = 40
_STRIKE_RANGE = 0.12  # near-money band (±12% of spot) pulled from the chain
_DELTA_LO, _DELTA_HI = 0.05, 0.95  # keep near-money strikes only

_UQ_COLS = ["symbol", "ts", "expiry_date", "strike"]
_UPDATE_COLS = ("dte", "iv", "delta", "spot")


def build_rows(
    source: OptionsDataSource,
    settings: Settings,
    *,
    as_of: date | None = None,
    symbols: list[str] | None = None,
) -> list[dict]:
    """One row per (symbol, expiry, near-money strike): the OTM-wing IV + delta."""
    as_of = as_of or eastern_now().date()
    symbols = symbols or settings.surface_symbols
    n_exp = settings.SURFACE_EXPIRIES

    records: list[dict] = []
    for symbol in symbols:
        try:
            chain = source.chain_long(symbol, max_exps=_MAX_EXPS, strike_range=_STRIKE_RANGE)
        except TradingIntelError as exc:
            log.warning("surface.chain_failed", symbol=symbol, error=str(exc))
            continue
        if chain is None or chain.empty:
            log.warning("surface.chain_empty", symbol=symbol)
            continue
        if not {"expiration", "strike", "delta", "iv"} <= set(chain.columns):
            log.warning("surface.missing_cols", symbol=symbol, cols=list(chain.columns))
            continue
        try:
            spot: float | None = float(source.spot(symbol))
        except TradingIntelError:
            spot = None

        df = chain.copy()
        for c in ("iv", "delta", "strike"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df["_exp"] = pd.to_datetime(df["expiration"], errors="coerce").dt.date
        df = df.dropna(subset=["iv", "delta", "strike", "_exp"])
        df = df[(df["iv"] > 0) & df["delta"].abs().between(_DELTA_LO, _DELTA_HI)]
        if df.empty:
            continue

        exps = sorted(x for x in df["_exp"].unique() if x is not None)[:n_exp]
        for exp in exps:
            g = df[df["_exp"] == exp]
            dte = int((exp - as_of).days)
            for strike, sg in g.groupby("strike"):
                # OTM wing: put (delta<0) below spot, call (delta>0) above; else |delta|<=0.5.
                if spot is not None:
                    otm = sg[sg["delta"] < 0] if strike < spot else sg[sg["delta"] > 0]
                else:
                    otm = sg[sg["delta"].abs() <= 0.5]
                pick = otm if not otm.empty else sg
                r = pick.iloc[0]
                records.append(
                    {
                        "symbol": symbol,
                        "ts": as_of,
                        "expiry_date": exp,
                        "dte": dte,
                        "strike": round(float(strike), 2),
                        "iv": float(r["iv"]),
                        "delta": float(r["delta"]),
                        "spot": spot,
                    }
                )
    return records


def _ensure_tickers(session: Session, symbols: set[str]) -> None:
    if not symbols:
        return
    stmt = pg_insert(Ticker).values([{"symbol": s} for s in sorted(symbols)])
    session.execute(stmt.on_conflict_do_nothing(index_elements=["symbol"]))


def _upsert(session: Session, records: list[dict]) -> None:
    if not records:
        return
    stmt = pg_insert(SurfaceSnapshot).values(records)
    stmt = stmt.on_conflict_do_update(
        index_elements=_UQ_COLS,
        set_={c: stmt.excluded[c] for c in _UPDATE_COLS},
    )
    session.execute(stmt)


def run(
    session: Session,
    source: OptionsDataSource,
    *,
    settings: Settings | None = None,
    symbols: list[str] | None = None,
) -> int:
    """Build today's per-strike surface and upsert it."""
    settings = settings or get_settings()
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="surface_snapshots")
    as_of = eastern_now().date()
    records = build_rows(source, settings, as_of=as_of, symbols=symbols)
    _ensure_tickers(session, {r["symbol"] for r in records})
    _upsert(session, records)
    session.commit()
    bound.info(
        "surface_snapshots.done",
        as_of=as_of.isoformat(),
        rows=len(records),
        symbols=len({r["symbol"] for r in records}),
    )
    return len(records)


def main() -> None:
    """Manual/NAS entrypoint: wire Settings -> session -> ConvexClient, run once."""
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
