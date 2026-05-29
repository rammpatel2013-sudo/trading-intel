"""Scheduled job (EOD): wide (~180d) per-strike chain snapshot for the OI study.

Pulls the long-dated chain (``chain_long`` — ~40 expirations, +/-20% of spot)
for every watchlist symbol, filters to expirations within ``window_days`` (180
by default), and writes one ``oi_chain_eod`` row per (expiry, strike, side) with
open interest, the vendor's day-over-day OI change (Convex ``oi_ch`` ->
``oi_change``), traded volume and signed greek-OI exposures. This is the daily
EOD snapshot the day-over-day positioning analytics diff against.

OI is an end-of-day figure (it settles overnight), so once-daily after the close
is the right cadence for a meaningful "vs yesterday" comparison. Idempotent:
``INSERT ... ON CONFLICT (symbol, ts, source, expiry, strike, cp) DO NOTHING``
(CLAUDE.md rule 5), with ``ts`` floored to the day so a same-day re-run does not
duplicate. Data collection only — emits no signals (FlashAlpha rule 4).

Manual run:
    python -m trading_intel.scheduler.jobs.oi_chain_eod
"""
from __future__ import annotations

import uuid
from datetime import datetime

import pandas as pd
import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from trading_intel.clients import OptionsDataSource
from trading_intel.config import Settings, get_settings
from trading_intel.errors import TradingIntelError
from trading_intel.memory.models import OiChainEod
from trading_intel.timeutils import eastern_now
from trading_intel.watchlist import effective_symbols

log = structlog.get_logger(__name__)

_SOURCE = "convex_eod"
_UQ_COLS = ["symbol", "ts", "source", "expiry", "strike", "cp"]
DEFAULT_WINDOW_DAYS = 180
# Postgres caps bound params at 65535/statement; ~16 cols/row -> stay well under
# (~4095 rows max). Batch the multi-row INSERT so a wide 180d chain doesn't blow it.
_INSERT_BATCH = 1000

_FLOAT_COLS = ("delta", "gamma", "iv", "gxoi", "dxoi", "vxoi")
_INT_COLS = ("oi", "oi_change", "volume")


def _chain_to_records(
    chain: pd.DataFrame,
    *,
    symbol: str,
    ts: datetime,
    window_days: int,
    source: str = _SOURCE,
) -> list[dict]:
    """Map a normalized ``chain_long`` frame to ``oi_chain_eod`` rows.

    Keeps only call/put rows with a real expiration/strike whose DTE is within
    ``[0, window_days]``, coerces numerics, and turns NaN into None so nullable
    columns store NULL. ``oi_change`` is the vendor's ``oi_ch`` (NaN if absent).
    """
    needed = {"expiration", "strike", "opt_kind"}
    if chain is None or chain.empty or not needed.issubset(chain.columns):
        return []

    df = chain[chain["expiration"].notna() & chain["strike"].notna()].copy()
    cp = df["opt_kind"].astype(str).str.upper().str[0]
    keep = cp.isin(["C", "P"])
    df, cp = df[keep], cp[keep]
    if df.empty:
        return []

    ref = pd.Timestamp(ts).date()
    dte = df["expiration"].map(
        lambda e: (pd.Timestamp(e).date() - ref).days if pd.notna(e) else None
    )
    dte = pd.to_numeric(dte, errors="coerce")
    window = (dte >= 0) & (dte <= window_days)
    df, cp, dte = df[window.fillna(False)], cp[window.fillna(False)], dte[window.fillna(False)]
    if df.empty:
        return []

    def col(name: str) -> pd.Series:
        if name in df.columns:
            return pd.to_numeric(df[name], errors="coerce")
        return pd.Series(float("nan"), index=df.index)

    out = pd.DataFrame(
        {
            "symbol": symbol,
            "ts": ts,
            "expiry": pd.to_datetime(df["expiration"]).dt.date,
            "strike": col("strike").astype(float),
            "cp": cp,
            "dte": dte.astype("Int64"),
            "source": source,
            **{name: col(name) for name in (*_INT_COLS, *_FLOAT_COLS)},
        }
    )
    out = out.astype(object).where(pd.notna(out), None)

    records = out.to_dict("records")
    for rec in records:
        for k in ("dte", *_INT_COLS):
            if rec[k] is not None:
                rec[k] = int(rec[k])
    return records


def run(
    session: Session,
    source: OptionsDataSource,
    *,
    settings: Settings | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    symbols: list[str] | None = None,
) -> None:
    """Snapshot the watchlist's wide EOD per-strike chain into ``oi_chain_eod``."""
    settings = settings or get_settings()
    correlation_id = uuid.uuid4().hex
    bound = log.bind(correlation_id=correlation_id, job="oi_chain_eod")

    ts = eastern_now().replace(hour=0, minute=0, second=0, microsecond=0)
    symbols = symbols or effective_symbols(session, settings)
    bound.info(
        "oi_chain_eod.start", ts=ts.isoformat(), symbol_count=len(symbols),
        window_days=window_days,
    )

    rows_written = 0
    failed = 0
    for symbol in symbols:
        try:
            chain = source.chain_long(symbol)  # type: ignore[attr-defined]
        except (TradingIntelError, AttributeError) as exc:
            failed += 1
            bound.warning("oi_chain_eod.symbol_failed", symbol=symbol, error=str(exc))
            continue

        records = _chain_to_records(chain, symbol=symbol, ts=ts, window_days=window_days)
        if not records:
            bound.warning("oi_chain_eod.empty", symbol=symbol)
            continue

        dialect = session.bind.dialect.name if session.bind is not None else "postgresql"
        _insert = sqlite_insert if dialect == "sqlite" else pg_insert
        for start in range(0, len(records), _INSERT_BATCH):
            batch = records[start : start + _INSERT_BATCH]
            stmt = _insert(OiChainEod).values(batch).on_conflict_do_nothing(
                index_elements=_UQ_COLS
            )
            session.execute(stmt)
        rows_written += len(records)
        bound.debug("oi_chain_eod.symbol", symbol=symbol, rows=len(records))

    session.commit()
    bound.info("oi_chain_eod.done", rows=rows_written, failed=failed)


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
        run(session, source, settings=settings)


if __name__ == "__main__":
    main()
