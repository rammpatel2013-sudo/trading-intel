"""Scheduled job (EOD): constant-maturity delta-vol surface (the ^SPX board).

Banks the FULL implied-vol smile (5Δ..50Δ ATM, both wings) interpolated onto
FIXED forward horizons — constant-maturity rungs ``VOL_SURFACE_CM_DTES``
(7/14/21/30/60/90d) — for ``VOL_SURFACE_CM_SYMBOLS`` (SPX) every EOD. Because
the rungs are constant-maturity, the "fixed timeframe" rolls forward on its own
(today's 90d ≈ Sep; a month on ≈ Oct) and the day-over-day / weekly vol CHANGE
is always same-horizon — no roll discontinuity.

Reuses the exact machinery behind ``iv_tenor_snapshots``: one wide ``chain_long``
pull → ``greeks.surface.build_delta_surface`` (12-delta grid, both wings) → the
total-variance ``cm_interp`` at each rung. Writes only the small aggregate rows
(no per-strike chain persisted, preserving the CHAIN_EXCLUDE_ROOTS storage
intent). ``near_expiry`` is the nearest real listed expiry to each rung (display
label only). Idempotent upsert. Regime descriptor (FlashAlpha rule 4) — no signal.

Manual run:
    python -m trading_intel.scheduler.jobs.vol_surface_cm
"""

from __future__ import annotations

import uuid
from datetime import date

import numpy as np
import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_intel.clients import OptionsDataSource
from trading_intel.config import Settings, get_settings
from trading_intel.errors import ComputationError, TradingIntelError
from trading_intel.greeks.surface import DEFAULT_DELTAS, build_delta_surface
from trading_intel.memory.models import Ticker, VolSurfaceCM
from trading_intel.scheduler.jobs.iv_tenor_snapshots import (
    _MAX_EXPIRIES,
    _MAX_EXPS,
    _STRIKE_RANGE,
    cm_interp,
)
from trading_intel.timeutils import eastern_now

log = structlog.get_logger(__name__)

_UQ_COLS = ["symbol", "ts", "dte", "delta", "side"]
_UPDATE_COLS = ("iv", "spot", "near_expiry")


def build_rows(
    source: OptionsDataSource,
    settings: Settings,
    *,
    as_of: date | None = None,
    symbols: list[str] | None = None,
) -> list[dict]:
    """Compute the day's ``vol_surface_cm`` rows from a live chain (no upsert)."""
    as_of = as_of or eastern_now().date()
    symbols = symbols or settings.vol_surface_cm_symbols
    rungs = settings.vol_surface_cm_dtes

    records: list[dict] = []
    for symbol in symbols:
        try:
            chain = source.chain_long(symbol, max_exps=_MAX_EXPS, strike_range=_STRIKE_RANGE)
        except TradingIntelError as exc:
            log.warning("vol_surface_cm.chain_failed", symbol=symbol, error=str(exc))
            continue
        if chain is None or chain.empty:
            log.warning("vol_surface_cm.chain_empty", symbol=symbol)
            continue

        try:
            surface = build_delta_surface(chain, deltas=DEFAULT_DELTAS, n_expiries=_MAX_EXPIRIES, ref=as_of)
        except ComputationError as exc:
            log.warning("vol_surface_cm.surface_failed", symbol=symbol, error=str(exc))
            continue
        if surface.n_expiries < 2:
            continue  # need a bracketing pair for the constant-maturity interp

        spot: float | None
        try:
            spot = float(source.spot(symbol))
        except TradingIntelError:
            spot = None

        dte = surface.dte  # (T,) ascending
        for rung in rungs:
            # nearest real listed expiry to this rung (display label only)
            near_expiry: date | None = None
            if len(dte):
                j = int(np.argmin(np.abs(dte - rung)))
                if 0 <= j < len(surface.expiries):
                    near_expiry = surface.expiries[j]
            for di, d in enumerate(surface.deltas):
                iv_call = cm_interp(dte, surface.iv_call[:, di], rung)
                iv_put = cm_interp(dte, surface.iv_put[:, di], rung)
                for side, iv in (("call", iv_call), ("put", iv_put)):
                    if iv is None:
                        continue
                    records.append(
                        {
                            "symbol": symbol,
                            "ts": as_of,
                            "dte": int(rung),
                            "delta": float(d),
                            "side": side,
                            "iv": float(iv),
                            "spot": spot,
                            "near_expiry": near_expiry,
                        }
                    )
    return records


def _ensure_tickers(session: Session, symbols: set[str]) -> None:
    if not symbols:
        return
    stmt = pg_insert(Ticker).values([{"symbol": s} for s in sorted(symbols)])
    stmt = stmt.on_conflict_do_nothing(index_elements=["symbol"])
    session.execute(stmt)


def _upsert(session: Session, records: list[dict]) -> None:
    if not records:
        return
    # chunk to stay well under the 65535 bound-param limit (9 cols/row)
    for i in range(0, len(records), 5000):
        batch = records[i : i + 5000]
        stmt = pg_insert(VolSurfaceCM).values(batch)
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
    as_of: date | None = None,
) -> None:
    """Build today's constant-maturity delta-vol surface rows and upsert them."""
    settings = settings or get_settings()
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="vol_surface_cm")

    as_of = as_of or eastern_now().date()
    records = build_rows(source, settings, as_of=as_of, symbols=symbols)
    _ensure_tickers(session, {r["symbol"] for r in records})
    _upsert(session, records)
    session.commit()
    bound.info(
        "vol_surface_cm.done",
        as_of=as_of.isoformat(),
        rows=len(records),
        symbols=len({r["symbol"] for r in records}),
    )


def main() -> None:
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
    with make_session_factory(settings)() as session:
        run(session, source, settings=settings)


if __name__ == "__main__":
    main()
