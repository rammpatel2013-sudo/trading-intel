"""Scheduled job: per-SPDR sector snapshot from CVForge (skew + walls + greeks).

For each of the 11 SPDR sector ETFs (config ``SECTOR_ROOTS``), pull the CVForge
chain ONCE (secondary source — NEVER Convex, so the 10/min Convex budget stays
reserved for the live regime engine, rule 1) and from that single frame write:

  * ``greeks_snapshots`` (source ``cvforge``): net GEX/DEX/gex_flip/dex_flip/ATM
    IV + flow enrichment — feeds the sector report's fragility/lead-lag read and
    the ATM-IV percentile history.
  * ``sector_snapshots``: 25Δ risk-reversal + call/put gamma walls + the
    near-money per-strike IV grid whose day-over-day diff is the fixed-strike
    "offered vs bid" footprint ("a wall is not a wall").

Per-symbol RETRY (+ a small inter-symbol pause) because CVForge serves the SPDR
chains intermittently — one pass now reliably lands all 11 instead of a partial
set. Idempotent on the unique keys (CLAUDE.md rule 5). Descriptor only (rule 4).

Manual run:
    python -m trading_intel.scheduler.jobs.sector_greeks
"""
from __future__ import annotations

import time
import uuid

import pandas as pd
import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_intel.clients import OptionsDataSource
from trading_intel.config import Settings, get_settings
from trading_intel.errors import TradingIntelError
from trading_intel.greeks.skew_walls import sector_extras
from trading_intel.market.sector_correlation import SECTOR_SPDRS
from trading_intel.memory.models import GreeksSnapshot, SectorSnapshot
from trading_intel.timeutils import eastern_now

log = structlog.get_logger(__name__)

_SOURCE = "cvforge"
_RETRIES = 2  # additional attempts after the first, per symbol
_PAUSE_S = 0.4  # inter-symbol pause to stay friendly to the CVForge endpoint


def _chain_with_retry(source: OptionsDataSource, symbol: str, bound) -> pd.DataFrame | None:
    """Pull one symbol's chain, retrying on empty/error (CVForge is intermittent)."""
    for attempt in range(_RETRIES + 1):
        try:
            df = source.chain(symbol)
        except TradingIntelError as exc:
            bound.warning("sector.chain_error", symbol=symbol, attempt=attempt, error=str(exc))
            df = None
        if df is not None and not df.empty:
            return df
        if attempt < _RETRIES:
            time.sleep(_PAUSE_S * (attempt + 1))
    return None


def run(
    session: Session,
    source: OptionsDataSource,
    *,
    settings: Settings | None = None,
    symbols: list[str] | None = None,
) -> None:
    """Snapshot the sector SPDRs via ``source`` (a ``CVForgeClient``) into both tables."""
    settings = settings or get_settings()
    roots = symbols or list(getattr(settings, "sector_roots", None) or SECTOR_SPDRS)
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="sector_greeks")

    now = eastern_now()
    ts = now.replace(second=0, microsecond=0)
    as_of = now.date()
    computed_at = now.replace(microsecond=0)
    bound.info("sector_greeks.start", symbol_count=len(roots), source=_SOURCE)

    written = failed = 0
    for i, symbol in enumerate(roots):
        if i:
            time.sleep(_PAUSE_S)
        df = _chain_with_retry(source, symbol, bound)
        if df is None or df.empty:
            failed += 1
            bound.warning("sector_greeks.empty", symbol=symbol)
            continue
        try:
            spot = float(df["underlying_price"].iloc[0])
            exposures = source.exposures(symbol, chain=df) or {}
            extras = sector_extras(df, spot, ref=as_of)
        except (TradingIntelError, KeyError, ValueError) as exc:
            failed += 1
            bound.warning("sector_greeks.compute_failed", symbol=symbol, error=str(exc))
            continue
        if not exposures:
            failed += 1
            bound.warning("sector_greeks.no_exposures", symbol=symbol)
            continue

        session.execute(
            pg_insert(GreeksSnapshot)
            .values(
                symbol=symbol,
                ts=ts,
                spot=exposures.get("spot", spot),
                gex_total=exposures.get("gex_total"),
                dex_total=exposures.get("dex_total"),
                vex_total=exposures.get("vex_total"),
                chex_total=exposures.get("chex_total"),
                gex_flip=exposures.get("gex_flip"),
                gex_rvol_ratio=None,
                atm_iv=exposures.get("atm_iv"),
                dex_flip=exposures.get("dex_flip"),
                call_volume=exposures.get("call_volume"),
                put_volume=exposures.get("put_volume"),
                call_notional=exposures.get("call_notional"),
                put_notional=exposures.get("put_notional"),
                source=_SOURCE,
            )
            .on_conflict_do_nothing(index_elements=["symbol", "ts", "source"])
        )
        session.execute(
            pg_insert(SectorSnapshot)
            .values(
                as_of=as_of,
                symbol=symbol,
                spot=spot,
                net_gex=exposures.get("gex_total"),
                net_dex=exposures.get("dex_total"),
                gex_flip=exposures.get("gex_flip"),
                atm_iv=exposures.get("atm_iv"),
                rr25=extras.get("rr25"),
                rr25_dte=extras.get("rr25_dte"),
                call_wall=extras.get("call_wall"),
                put_wall=extras.get("put_wall"),
                strike_iv=extras.get("strike_iv"),
                computed_at=computed_at,
                source=_SOURCE,
            )
            .on_conflict_do_nothing(index_elements=["as_of", "symbol", "source"])
        )
        written += 1
        bound.debug(
            "sector_greeks.row",
            symbol=symbol,
            gex_total=exposures.get("gex_total"),
            rr25=extras.get("rr25"),
            call_wall=extras.get("call_wall"),
            put_wall=extras.get("put_wall"),
        )

    session.commit()
    bound.info("sector_greeks.done", written=written, failed=failed)


def main() -> None:
    """Manual/scheduled entrypoint: wire Settings -> session -> CVForgeClient, run once."""
    from trading_intel.clients.cvforge import CVForgeClient
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
    source = CVForgeClient(settings)
    session_factory = make_session_factory(settings)
    with session_factory() as session:
        run(session, source, settings=settings)


if __name__ == "__main__":
    main()
