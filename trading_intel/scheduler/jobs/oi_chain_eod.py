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

2026-11-21 SPX full-chain step (folds in scripts/eod_chain.py, DSM "TI EOD chain"): SPX/SPXW are excluded from the
per-strike watchlist loop (CHAIN_EXCLUDE_ROOTS), but the position ledger needs EVERY SPX expiry with the day's
signed volume. ``_spx_full`` pulls ``full_chain`` (every listed expiry, +-50%, volm/value buy+sell) and writes it
under source ``cvx_full`` (PM) / ``cvx_full_set`` (AM-settled). The name deliberately does NOT start with
"convex": every reader filters ``source LIKE 'convex%'``, so these rows never double the SPX 08:35 book. Not
pruned (the ledger needs the history). ~28k rows a night.

2026-11-21 (later) SPX closing book: the same pull is ALSO written as an ordinary SPX EOD book - source
``convex_eod`` / ``convex_eod_set``, expiries <= 180 d, no signed columns - stamped at 16:00 of the day
(``EOD_BOOK_HOUR``), NOT 00:00. Why: every SPX tool read only the 08:35 book (pre-open quotes) all evening and
all weekend. Why 16:00: the 08:35 book keeps its 00:00 stamp and its history (am_feed / opex_split / the AM
card read ``convex_am`` by name), and a different ``ts`` means no reader ever sums the two books. Readers that
take the newest book get the close after ~17:15 and the next morning's 08:35 book from 08:35.
``book_spot`` treats it as an EOD book (spot = last spot on that day).

Manual run:
    python -m trading_intel.scheduler.jobs.oi_chain_eod
    python -m trading_intel.scheduler.jobs.oi_chain_eod --full-only     # just the SPX full-chain step
"""
from __future__ import annotations

import uuid
from datetime import datetime

import pandas as pd
import structlog
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from trading_intel.clients import OptionsDataSource
from trading_intel.config import Settings, get_settings
from trading_intel.errors import TradingIntelError
from trading_intel.memory.models import OiChainEod
from trading_intel.timeutils import eastern_now, is_trading_session
from trading_intel.watchlist import effective_symbols

log = structlog.get_logger(__name__)

_SOURCE = "convex_eod"
_UQ_COLS = ["symbol", "ts", "source", "expiry", "strike", "cp"]
DEFAULT_WINDOW_DAYS = 180
FULL_SOURCE = "cvx_full"
FULL_SYMBOLS = ("SPX",)
FULL_WINDOW_DAYS = 4000   # every listed expiry (SPX LEAPS run ~5 years)
# SPX closing book written from the same pull (see module doc): cvx_full -> convex_eod, stamped 16:00.
EOD_BOOK_HOUR = 16
_BOOK_SOURCE = {FULL_SOURCE: _SOURCE, FULL_SOURCE + "_set": _SOURCE + "_set"}
# Postgres caps bound params at 65535/statement; ~22 cols/row -> stay well under
# (~2978 rows max after the 2026-10-25 widening). Batch the multi-row INSERT so a
# wide 180d chain doesn't blow it.
_INSERT_BATCH = 1000

# AM-settled SPX roots: the 3rd-Friday monthly (.SPX) and, from 2026-11-18, the Wednesday/Friday
# AM weeklies (.SPXO). Tagged "<source>_set"; PM weeklies/dailies (.SPXW) keep "<source>".
AM_SETTLED_ROOTS = ("SPX", "SPXO")

_FLOAT_COLS = (
    "delta",
    "gamma",
    "iv",
    "gxoi",
    "dxoi",
    "vxoi",
    # 2026-10-25: quotes + aggressor-split greek flow. All optional at the
    # client (a symbol that doesn't serve them yields NaN -> NULL here), so a
    # missing field never costs us the row.
    "bid_price",
    "ask_price",
    "gammas_buy",
    "gammas_sell",
    "deltas_buy",
    "deltas_sell",
)
# vendor name -> column (0070). NaN / absent -> NULL, like every optional field.
_SIGNED_COLS = {"volm_buy": "vol_buy", "volm_sell": "vol_sell", "value_buy": "val_buy", "value_sell": "val_sell"}
_INT_COLS = ("oi", "oi_change", "volume")


def _chain_to_records(
    chain: pd.DataFrame,
    *,
    symbol: str,
    ts: datetime,
    window_days: int,
    source: str = _SOURCE,
    signed: bool = False,
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

    # AM-settled SPX monthlies (3rd-Friday, contract root ``.SPX``) vs the PM-settled
    # weeklies (``.SPXW``): probed 2026-10-22 — Convex's contract symbol carries the
    # root (``.SPX261120C7425`` vs ``.SPXW261022C7425``). Tag the AM-settled rows with
    # their own ``source`` so the OpEx-morning NOW (AM+PM) vs POST-SET (PM only) split
    # is a WHERE clause, no migration (``source`` is already in the unique key).
    # 2026-11-10: SPXO = Cboe's new AM-settled SPX WEEKLIES (first listed 11/18 + 12/16). Convex lists
    # them inside the SPX chain as their own group on the same date as that day's SPXW group, so
    # without a tag the two collided on the unique key and ON CONFLICT DO NOTHING silently dropped
    # the SPXW rows. Both AM roots go under "<source>_set".
    src = pd.Series(source, index=df.index)
    if symbol.upper() == "SPX" and "symbol" in df.columns:
        root = df["symbol"].astype(str).str.extract(r"^\.([A-Z]+)\d")[0]
        src = src.where(~root.isin(AM_SETTLED_ROOTS), source + "_set")

    out = pd.DataFrame(
        {
            "symbol": symbol,
            "ts": ts,
            "expiry": pd.to_datetime(df["expiration"]).dt.date,
            "strike": col("strike").astype(float),
            "cp": cp,
            "dte": dte.astype("Int64"),
            "source": src,
            **{name: col(name) for name in (*_INT_COLS, *_FLOAT_COLS)},
            **({out: col(vendor) for vendor, out in _SIGNED_COLS.items()} if signed else {}),
        }
    )
    out = out.astype(object).where(pd.notna(out), None)

    records = out.to_dict("records")
    for rec in records:
        for k in ("dte", *_INT_COLS):
            if rec[k] is not None:
                rec[k] = int(rec[k])
    return records


def _insert_records(session: Session, records: list[dict]) -> None:
    dialect = session.bind.dialect.name if session.bind is not None else "postgresql"
    _insert = sqlite_insert if dialect == "sqlite" else pg_insert
    for start in range(0, len(records), _INSERT_BATCH):
        batch = records[start : start + _INSERT_BATCH]
        session.execute(_insert(OiChainEod).values(batch).on_conflict_do_nothing(index_elements=_UQ_COLS))


def _closing_book(records: list[dict], ts: datetime) -> list[dict]:
    """The ``cvx_full`` rows re-tagged as the day's SPX EOD book (``convex_eod*``, <= 180 d, 16:00 stamp)."""
    book_ts = ts.replace(hour=EOD_BOOK_HOUR, minute=0, second=0, microsecond=0)
    signed = set(_SIGNED_COLS.values())
    out = []
    for r in records:
        if r["dte"] is None or r["dte"] > DEFAULT_WINDOW_DAYS or r["source"] not in _BOOK_SOURCE:
            continue
        rec = {k: v for k, v in r.items() if k not in signed}
        rec["source"] = _BOOK_SOURCE[r["source"]]
        rec["ts"] = book_ts
        out.append(rec)
    return out


def _spx_full(session: Session, source: OptionsDataSource, ts: datetime, bound) -> int:
    """SPX every-expiry chain + signed day volume -> ``cvx_full`` rows. Returns rows written (0 on failure)."""
    written = 0
    for symbol in FULL_SYMBOLS:
        try:
            chain = source.full_chain(symbol)  # type: ignore[attr-defined]
        except (TradingIntelError, AttributeError) as exc:
            bound.warning("oi_chain_eod.full_failed", symbol=symbol, error=str(exc))
            continue
        records = _chain_to_records(chain, symbol=symbol, ts=ts, window_days=FULL_WINDOW_DAYS,
                                    source=FULL_SOURCE, signed=True)
        if not records:
            bound.warning("oi_chain_eod.full_empty", symbol=symbol)
            continue
        _insert_records(session, records)
        book = _closing_book(records, ts)
        if book:
            _insert_records(session, book)
        session.commit()
        n_signed = sum(1 for r in records if (r.get("vol_buy") or 0) or (r.get("vol_sell") or 0))
        exps = sorted({r["expiry"] for r in records})
        bound.info("oi_chain_eod.full", symbol=symbol, rows=len(records), expiries=len(exps),
                   last_expiry=exps[-1].isoformat(), rows_with_signed_volume=n_signed,
                   closing_book_rows=len(book), closing_book_ts=ts.replace(hour=EOD_BOOK_HOUR).isoformat())
        written += len(records)
    return written


def run(
    session: Session,
    source: OptionsDataSource,
    *,
    settings: Settings | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    symbols: list[str] | None = None,
    full_only: bool = False,
) -> None:
    """Snapshot the watchlist's wide EOD per-strike chain into ``oi_chain_eod`` (+ the SPX full chain first)."""
    settings = settings or get_settings()
    correlation_id = uuid.uuid4().hex
    bound = log.bind(correlation_id=correlation_id, job="oi_chain_eod")

    as_of = eastern_now().date()
    if not is_trading_session(as_of):
        # Weekend/holiday: Convex serves stale Friday data — writing it under a
        # Saturday ts creates ghost rows the day-over-day diff interprets as real
        # OI changes (2026-10-26 #66). Same guard breadth/vix_snapshot use.
        bound.info("oi_chain_eod.skipped_non_session", as_of=as_of.isoformat())
        return

    ts = eastern_now().replace(hour=0, minute=0, second=0, microsecond=0)
    # SPX full chain FIRST (2 requests, ~10 s): the watchlist loop below takes ~1 h at 7 calls/min.
    if symbols is None:
        full_rows = _spx_full(session, source, ts, bound)
        bound.info("oi_chain_eod.full_done", rows=full_rows)
    if full_only:
        return
    symbols = symbols or effective_symbols(session, settings)
    # Index ETFs we only want the aggregate GEX/DEX line for stay out of the
    # heavy per-strike persistence (greeks_snapshot still collects them).
    _exclude = getattr(settings, "chain_exclude_roots", set())
    symbols = [s for s in symbols if s not in _exclude]
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

        # The 08:35 pull (index_walls_am: stocks with a 0DTE) shares this day's ts.
        # The EOD book supersedes it, so readers never sum two books for one day.
        session.execute(
            delete(OiChainEod).where(
                OiChainEod.symbol == symbol, OiChainEod.ts == ts,
                OiChainEod.source.in_(("convex_am", "convex_am_set")),
            )
        )
        _insert_records(session, records)
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

    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--full-only", action="store_true", help="only the SPX full-chain step")
    args = ap.parse_args()
    source = ConvexClient(settings)
    session_factory = make_session_factory(settings)
    with session_factory() as session:
        run(session, source, settings=settings, full_only=args.full_only)


if __name__ == "__main__":
    main()
