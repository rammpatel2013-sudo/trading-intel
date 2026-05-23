"""Scheduled job: intraday 0DTE/1DTE volume-weighted flow for SPX/SPY/QQQ.

Every 5 minutes during the regular session this pulls a tight (±range) chain
for the focused symbol set, restricts it to 0DTE/1DTE, and stores one
``intraday_flow`` row per (expiry, strike, side) carrying the raw greeks, the
traded volume, and the volume-weighted gamma/delta/vanna/charm — on both
cumulative day volume (``*_vol``) and the per-cycle increment vs the previous
snapshot (``*_vol_iv``).

Idempotent: ``INSERT ... ON CONFLICT (symbol, ts, source, expiry, strike, cp)
DO NOTHING`` (CLAUDE.md rule 5), ``ts`` floored to the 5-minute slot so a
re-run of the same slot is a no-op. The interval (fresh-flow) weighting needs
the previous slot's rows, which are read back from ``intraday_flow``.

Data collection only — emits no signals/alerts (FlashAlpha rule 4).

Manual run (ignores the market-hours guard):
    python -m trading_intel.scheduler.jobs.intraday_flow
"""
from __future__ import annotations

import uuid
from datetime import datetime

import pandas as pd
import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_intel.clients import OptionsDataSource
from trading_intel.config import Settings, get_settings
from trading_intel.errors import TradingIntelError
from trading_intel.greeks.intraday_flow import (
    filter_0dte_1dte,
    interval_volume,
    is_market_hours,
    volume_weighted_by_strike,
)
from trading_intel.memory.models import IntradayFlow

log = structlog.get_logger(__name__)

_SOURCE = "convex"
_UQ_COLS = ["symbol", "ts", "source", "expiry", "strike", "cp"]
_SLOT_MINUTES = 5


def _floor_to_slot(now: datetime, minutes: int = _SLOT_MINUTES) -> datetime:
    """Floor ``now`` to the start of its ``minutes``-minute slot (drop seconds)."""
    floored_min = (now.minute // minutes) * minutes
    return now.replace(minute=floored_min, second=0, microsecond=0)


def _load_prev_snapshot(session: Session, symbol: str, ts: datetime) -> pd.DataFrame:
    """Most-recent stored ``intraday_flow`` rows for ``symbol`` strictly before ``ts``.

    Returned with the chain vocabulary (``expiry``/``strike``/``opt_kind``/
    ``volume``) so :func:`interval_volume` can diff against it.
    """
    prev_ts = session.execute(
        select(IntradayFlow.ts)
        .where(IntradayFlow.symbol == symbol, IntradayFlow.source == _SOURCE, IntradayFlow.ts < ts)
        .order_by(IntradayFlow.ts.desc())
        .limit(1)
    ).scalar_one_or_none()
    if prev_ts is None:
        return pd.DataFrame()
    rows = list(
        session.execute(
            select(IntradayFlow).where(
                IntradayFlow.symbol == symbol,
                IntradayFlow.source == _SOURCE,
                IntradayFlow.ts == prev_ts,
            )
        ).scalars()
    )
    return pd.DataFrame(
        [
            {
                "expiry": pd.Timestamp(r.expiry),
                "strike": r.strike,
                "opt_kind": "call" if str(r.cp).upper().startswith("C") else "put",
                "volume": r.volume,
            }
            for r in rows
        ]
    )


def _build_records(
    chain: pd.DataFrame, prev: pd.DataFrame, *, symbol: str, ts: datetime, spot: float
) -> list[dict]:
    """Per-strike+side ``intraday_flow`` rows with both volume-weighted bases."""
    if chain is None or chain.empty:
        return []
    df = interval_volume(chain, prev)

    # Per-strike weighted exposures on each volume basis, merged back by strike.
    cum = volume_weighted_by_strike(df, spot, volume_col="volume").set_index("strike")
    ivl = volume_weighted_by_strike(df, spot, volume_col="volume_interval").set_index("strike")

    records: list[dict] = []
    for _, row in df.iterrows():
        cp = str(row["opt_kind"]).upper()[:1]
        if cp not in ("C", "P"):
            continue
        strike = float(row["strike"])
        cum_r = cum.loc[strike] if strike in cum.index else None
        ivl_r = ivl.loc[strike] if strike in ivl.index else None
        vol = row.get("volume")
        vol_iv = row.get("volume_interval")
        records.append(
            {
                "symbol": symbol,
                "ts": ts,
                "source": _SOURCE,
                "expiry": pd.to_datetime(row["expiration"]).date(),
                "dte": int(row["dte"]) if pd.notna(row.get("dte")) else None,
                "strike": strike,
                "cp": cp,
                "spot": spot,
                "iv": _f(row.get("iv")),
                "gamma": _f(row.get("gamma")),
                "delta": _f(row.get("delta")),
                "vanna": _f(row.get("vanna")),
                "charm": _f(row.get("charm")),
                "volume": int(vol) if pd.notna(vol) else None,
                "volume_interval": int(vol_iv) if pd.notna(vol_iv) else None,
                "gamma_vol": _f(cum_r["gamma_vol"]) if cum_r is not None else None,
                "delta_vol": _f(cum_r["delta_vol"]) if cum_r is not None else None,
                "vanna_vol": _f(cum_r["vanna_vol"]) if cum_r is not None else None,
                "charm_vol": _f(cum_r["charm_vol"]) if cum_r is not None else None,
                "gamma_vol_iv": _f(ivl_r["gamma_vol"]) if ivl_r is not None else None,
                "delta_vol_iv": _f(ivl_r["delta_vol"]) if ivl_r is not None else None,
                "vanna_vol_iv": _f(ivl_r["vanna_vol"]) if ivl_r is not None else None,
                "charm_vol_iv": _f(ivl_r["charm_vol"]) if ivl_r is not None else None,
            }
        )
    return records


def _f(value: object) -> float | None:
    """Coerce to float, mapping NaN/None to None for nullable columns."""
    if value is None:
        return None
    num = pd.to_numeric(value, errors="coerce")
    return float(num) if pd.notna(num) else None


def run(
    session: Session,
    source: OptionsDataSource,
    *,
    settings: Settings | None = None,
    force: bool = False,
) -> None:
    """Collect one intraday 0DTE/1DTE volume-flow snapshot for the focused set.

    Args:
        session: an open SQLAlchemy session (committed here).
        source: any ``OptionsDataSource`` implementation.
        settings: optional override; defaults to the process settings.
        force: bypass the market-hours guard (for manual runs / tests).
    """
    settings = settings or get_settings()
    correlation_id = uuid.uuid4().hex
    bound = log.bind(correlation_id=correlation_id, job="intraday_flow")

    now = datetime.now()
    if not force and not is_market_hours(now):
        bound.info("intraday_flow.skipped_off_hours", now=now.isoformat())
        return

    ts = _floor_to_slot(now)
    symbols = settings.intraday_symbols
    rng = settings.INTRADAY_STRIKE_RANGE
    max_dte = settings.INTRADAY_MAX_DTE
    bound.info("intraday_flow.start", ts=ts.isoformat(), symbols=symbols, rng=rng)

    written = 0
    failed = 0
    for symbol in symbols:
        try:
            raw = source.chain(symbol, strike_range=rng)
            spot = source.spot(symbol)
        except TradingIntelError as exc:
            failed += 1
            bound.warning("intraday_flow.symbol_failed", symbol=symbol, error=str(exc))
            continue

        chain = filter_0dte_1dte(raw, max_dte=max_dte)
        if chain is None or chain.empty:
            bound.warning("intraday_flow.empty", symbol=symbol)
            continue

        prev = _load_prev_snapshot(session, symbol, ts)
        records = _build_records(chain, prev, symbol=symbol, ts=ts, spot=spot)
        if not records:
            continue

        stmt = pg_insert(IntradayFlow).values(records).on_conflict_do_nothing(
            index_elements=_UQ_COLS
        )
        session.execute(stmt)
        written += len(records)
        bound.debug("intraday_flow.symbol", symbol=symbol, rows=len(records), spot=spot)

    session.commit()
    bound.info("intraday_flow.done", rows=written, failed=failed)


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
