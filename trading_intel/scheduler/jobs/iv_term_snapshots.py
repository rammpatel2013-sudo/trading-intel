"""Scheduled job (EOD): constant-maturity per-NAME IV term structure.

The per-name complement to ``iv_tenor_snapshots`` (which covers the index ETFs via a
live pull, since SPY/QQQ/SPX have no stored chain). Single names already have a stored
per-strike chain (``oi_chain_eod``), so this reads that surface — exactly like
``skew_snapshots`` — and interpolates a CONSTANT-MATURITY IV term (ATM + 15/25Δ wings)
in total-variance space at each configured tenor via the shared ``cm_interp``.

Why it exists: ``skew_snapshots.atm_iv`` is a *nearest-expiry* read, so its term line
sawtooths as expiries roll; this constant-maturity term never does, giving a clean
term-slope / backwardation trend per name.

Writes into the SAME ``iv_tenor_snapshots`` table (a disjoint symbol set from the index
job), so ``get_iv_tenor`` surfaces per-name term with no new plumbing. Reads stored data
only — no vendor call, no FMP. Idempotent upsert on (symbol, ts, tenor_dte) (CLAUDE.md
rule 5). Regime descriptor only (FlashAlpha rule 4).

Manual run:
    python -m trading_intel.scheduler.jobs.iv_term_snapshots
"""

from __future__ import annotations

import uuid
from datetime import date

import numpy as np
import pandas as pd
import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_intel.config import Settings, get_settings
from trading_intel.errors import ComputationError
from trading_intel.greeks.surface import build_delta_surface
from trading_intel.memory.models import IvTenorSnapshot, OiChainEod, Ticker
from trading_intel.scheduler.jobs.iv_tenor_snapshots import cm_interp
from trading_intel.timeutils import eastern_now
from trading_intel.watchlist import effective_symbols

log = structlog.get_logger(__name__)

_UQ_COLS = ["symbol", "ts", "tenor_dte"]
_UPDATE_COLS = (
    "iv_atm",
    "iv_call_15d",
    "iv_put_15d",
    "iv_call_25d",
    "iv_put_25d",
    "spot",
    "n_expiries",
)


def _delta_col(deltas: np.ndarray, target: float) -> int:
    """Column index of the delta grid point nearest ``target`` (percent)."""
    return int(np.argmin(np.abs(deltas - float(target))))


def _latest_chain(session: Session, symbol: str) -> pd.DataFrame | None:
    """Latest ``oi_chain_eod`` chain for ``symbol``, shaped for the delta surface.

    Mirrors ``skew_snapshots._latest_chain`` — stored data only, no vendor call.
    """
    ts = session.execute(
        select(OiChainEod.ts)
        .where(OiChainEod.symbol == symbol)
        .order_by(OiChainEod.ts.desc())
        .limit(1)
    ).scalar_one_or_none()
    if ts is None:
        return None
    rows = session.execute(
        select(OiChainEod.cp, OiChainEod.iv, OiChainEod.delta, OiChainEod.expiry).where(
            OiChainEod.symbol == symbol,
            OiChainEod.ts == ts,
            OiChainEod.iv.is_not(None),
        )
    ).all()
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["cp", "iv", "delta", "expiry"]).dropna(
        subset=["iv", "expiry"]
    )
    if df.empty:
        return None
    df["opt_kind"] = df["cp"]
    df["expiration"] = pd.to_datetime(df["expiry"])
    return df


def build_rows(
    session: Session,
    settings: Settings,
    *,
    as_of: date | None = None,
    symbols: list[str] | None = None,
) -> list[dict]:
    """Compute the day's per-name constant-maturity IV-term rows (no upsert)."""
    as_of = as_of or eastern_now().date()
    symbols = symbols or effective_symbols(session, settings)
    tenors = settings.iv_term_dtes
    deltas = settings.iv_tenor_deltas
    d15 = min(deltas, key=lambda x: abs(x - 15.0))
    d25 = min(deltas, key=lambda x: abs(x - 25.0))

    records: list[dict] = []
    for symbol in symbols:
        chain = _latest_chain(session, symbol)
        if chain is None:
            continue
        try:
            surface = build_delta_surface(chain, ref=as_of)
        except ComputationError:
            continue
        if surface.n_expiries < 2:
            continue  # need a bracketing pair for the constant-maturity interp

        c15 = _delta_col(surface.deltas, d15)
        c25 = _delta_col(surface.deltas, d25)
        atm, dte = surface.atm_iv, surface.dte

        for tenor in tenors:
            iv_atm = cm_interp(dte, atm, tenor)
            iv_call_15d = cm_interp(dte, surface.iv_call[:, c15], tenor)
            iv_put_15d = cm_interp(dte, surface.iv_put[:, c15], tenor)
            iv_call_25d = cm_interp(dte, surface.iv_call[:, c25], tenor)
            iv_put_25d = cm_interp(dte, surface.iv_put[:, c25], tenor)
            if all(
                x is None
                for x in (iv_atm, iv_call_15d, iv_put_15d, iv_call_25d, iv_put_25d)
            ):
                continue  # nothing interpolated at this tenor — skip the row

            records.append(
                {
                    "symbol": symbol,
                    "ts": as_of,
                    "tenor_dte": int(tenor),
                    "iv_atm": iv_atm,
                    "iv_call_15d": iv_call_15d,
                    "iv_put_15d": iv_put_15d,
                    "iv_call_25d": iv_call_25d,
                    "iv_put_25d": iv_put_25d,
                    "spot": None,  # stored-data job — spot is diagnostic only
                    "n_expiries": int(surface.n_expiries),
                }
            )
    return records


def _ensure_tickers(session: Session, symbols: set[str]) -> None:
    """Idempotently seed ``tickers`` rows so the snapshot FK holds."""
    if not symbols:
        return
    stmt = pg_insert(Ticker).values([{"symbol": s} for s in sorted(symbols)])
    session.execute(stmt.on_conflict_do_nothing(index_elements=["symbol"]))


def _upsert(session: Session, records: list[dict]) -> None:
    """Idempotent upsert into ``iv_tenor_snapshots`` (refresh on the natural key)."""
    if not records:
        return
    stmt = pg_insert(IvTenorSnapshot).values(records)
    stmt = stmt.on_conflict_do_update(
        index_elements=_UQ_COLS,
        set_={c: stmt.excluded[c] for c in _UPDATE_COLS},
    )
    session.execute(stmt)


def run(
    session: Session,
    *,
    settings: Settings | None = None,
    symbols: list[str] | None = None,
) -> int:
    """Build today's per-name constant-maturity IV-term rows and upsert them."""
    settings = settings or get_settings()
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="iv_term_snapshots")
    as_of = eastern_now().date()
    records = build_rows(session, settings, as_of=as_of, symbols=symbols)
    _ensure_tickers(session, {r["symbol"] for r in records})
    _upsert(session, records)
    session.commit()
    n_symbols = len({r["symbol"] for r in records})
    bound.info(
        "iv_term_snapshots.done",
        as_of=as_of.isoformat(),
        rows=len(records),
        symbols=n_symbols,
    )
    return len(records)


def main() -> None:
    """Manual/NAS entrypoint: wire Settings -> session, run once (no vendor client)."""
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
    session_factory = make_session_factory(settings)
    with session_factory() as session:
        run(session, settings=settings)


if __name__ == "__main__":
    main()
