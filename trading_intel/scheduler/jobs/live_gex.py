"""Scheduled job (intraday): live per-strike GEX refresh, delta-band filtered.

Every ~10 minutes during RTH, pulls a near-the-money chain for the configured
symbols, keeps only the |delta| 0.30-0.70 band (where the gamma that matters
sits), and stores one ``live_gex`` row per (symbol, ts, strike, cp) with the
chain's gxoi/dxoi/gamma/delta/iv + spot. This is the LIVE tier of GEX — pruned at
EOD by ``prune_live_gex``; the daily ``greeks_chain`` / ``greeks_snapshots`` stay
as the historical record.

Symbols default to the effective watchlist (set ``LIVE_GEX_SYMBOLS`` to a comma
list to scope down on Convex load). Idempotent ``ON CONFLICT (symbol, ts, strike,
cp) DO UPDATE`` with ``ts`` floored to the slot. Descriptor only (rule 4).

Manual run:
    python -m trading_intel.scheduler.jobs.live_gex
"""
from __future__ import annotations

import uuid
from datetime import datetime

import numpy as np
import pandas as pd
import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_intel.clients import OptionsDataSource
from trading_intel.config import Settings, get_settings
from trading_intel.errors import TradingIntelError
from trading_intel.greeks.intraday_flow import filter_delta_band, is_market_hours
from trading_intel.memory.models import LiveGex
from trading_intel.timeutils import eastern_now
from trading_intel.watchlist import effective_symbols

log = structlog.get_logger(__name__)

_SLOT_MINUTES = 10
_SOURCE = "convex"
_UQ_COLS = ["symbol", "ts", "strike", "cp", "expiry"]
_UPDATE_COLS = ("spot", "delta", "gamma", "iv", "gxoi", "dxoi", "oi", "vanna", "charm")
_SUM_COLS = ("gxoi", "dxoi", "oi")
_WMEAN_COLS = ("delta", "gamma", "iv", "vanna", "charm")
_GROUP_COLS = ["strike", "cp", "expiry"]


def _floor_to_slot(now: datetime, minutes: int = _SLOT_MINUTES) -> datetime:
    return now.replace(minute=(now.minute // minutes) * minutes, second=0, microsecond=0)


def _expiry_dates(df: pd.DataFrame) -> pd.Series:
    """Per-row option expiration as a python ``date`` (None when unavailable).

    The Convex client normalizes ``expiration`` to a datetime; we also tolerate
    raw epoch-day integers (Convex's native format) for safety.
    """
    if "expiration" not in df.columns:
        return pd.Series([None] * len(df), index=df.index)
    raw = df["expiration"]
    if pd.api.types.is_numeric_dtype(raw):
        parsed = pd.to_datetime(pd.to_numeric(raw, errors="coerce"), unit="D",
                                origin="unix", errors="coerce")
    else:
        parsed = pd.to_datetime(raw, errors="coerce")
    return pd.Series(
        [d.date() if pd.notna(d) else None for d in parsed], index=df.index
    )


def _collapse_by_strike(work: pd.DataFrame) -> pd.DataFrame:
    """One row per ``(strike, cp, expiry)`` — the ``live_gex`` grain.

    A near-the-money chain can list the same ``(strike, cp)`` more than once per
    expiry, and the upsert key is ``(symbol, ts, strike, cp, expiry)``, so rows
    sharing that key must be merged here or Postgres raises ``CardinalityViolation``.
    Different expiries are kept as separate rows (per-expiry decomposition).
    ``gxoi``/``dxoi``/``oi`` are additive (summed); the raw per-contract greeks
    are OI-weighted so that ``greek * oi`` at read time reconstructs the true
    total, falling back to a plain mean when a strike carries no OI.
    """
    w = work["oi"].fillna(0.0)
    wframe = work.assign(_w=w, **{f"_wn_{c}": work[c] * w for c in _WMEAN_COLS})
    g = wframe.groupby(_GROUP_COLS, sort=True, dropna=False)
    out = g[list(_SUM_COLS)].sum(min_count=1)
    wsum = g["_w"].sum()
    wn = g[[f"_wn_{c}" for c in _WMEAN_COLS]].sum()
    plain = g[list(_WMEAN_COLS)].mean()
    for c in _WMEAN_COLS:
        weighted = wn[f"_wn_{c}"] / wsum.replace(0.0, np.nan)
        out[c] = weighted.fillna(plain[c])
    return out.reset_index()


def _symbols(session: Session, settings: Settings) -> list[str]:
    raw = (settings.LIVE_GEX_SYMBOLS or "").strip()
    if raw:
        return [s.strip().upper() for s in raw.split(",") if s.strip()]
    return effective_symbols(session, settings)


def build_records(
    chain: pd.DataFrame, *, symbol: str, ts: datetime, spot: float | None,
    lo: float, hi: float,
) -> list[dict]:
    """Delta-band per-strike ``live_gex`` rows from a chain (NaN -> None)."""
    band = filter_delta_band(chain, lo=lo, hi=hi)
    needed = {"opt_kind", "strike"}
    if band is None or band.empty or not needed.issubset(band.columns):
        return []
    cp = band["opt_kind"].astype(str).str.upper().str[0]
    keep = cp.isin(["C", "P"])
    df, cp = band[keep], cp[keep]
    if df.empty:
        return []

    def col(name: str) -> pd.Series:
        return pd.to_numeric(df[name], errors="coerce") if name in df.columns else pd.Series(
            float("nan"), index=df.index
        )

    work = pd.DataFrame(
        {
            "strike": col("strike").astype(float), "cp": cp.to_numpy(),
            "expiry": _expiry_dates(df).to_numpy(),
            "gxoi": col("gxoi"), "dxoi": col("dxoi"), "oi": col("oi"),
            "delta": col("delta"), "gamma": col("gamma"), "iv": col("iv"),
            "vanna": col("vanna"), "charm": col("charm"),
        }
    )
    out = _collapse_by_strike(work)
    out.insert(0, "symbol", symbol)
    out.insert(1, "ts", ts)
    out.insert(2, "source", _SOURCE)
    out["spot"] = float(spot) if spot is not None else None
    out = out.astype(object).where(pd.notna(out), None)
    return out.to_dict("records")


def run(
    session: Session,
    source: OptionsDataSource,
    *,
    settings: Settings | None = None,
    force: bool = False,
) -> None:
    """Snapshot the live delta-band GEX for the configured symbols into ``live_gex``."""
    settings = settings or get_settings()
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="live_gex")

    now = eastern_now()
    if not force and not is_market_hours(now):
        bound.info("live_gex.skipped_off_hours", now=now.isoformat())
        return

    ts = _floor_to_slot(now)
    symbols = _symbols(session, settings)
    bound.info("live_gex.start", ts=ts.isoformat(), symbol_count=len(symbols))

    written = 0
    failed = 0
    for symbol in symbols:
        try:
            chain = source.chain(symbol, strike_range=settings.LIVE_GEX_STRIKE_RANGE)
            spot = source.spot(symbol)
        except (TradingIntelError, AttributeError) as exc:
            failed += 1
            bound.warning("live_gex.symbol_failed", symbol=symbol, error=str(exc))
            continue
        records = build_records(
            chain, symbol=symbol, ts=ts, spot=spot,
            lo=settings.LIVE_GEX_DELTA_LO, hi=settings.LIVE_GEX_DELTA_HI,
        )
        if not records:
            continue
        stmt = pg_insert(LiveGex).values(records)
        stmt = stmt.on_conflict_do_update(
            index_elements=_UQ_COLS, set_={c: stmt.excluded[c] for c in _UPDATE_COLS}
        )
        session.execute(stmt)
        written += len(records)

    session.commit()
    bound.info("live_gex.done", rows=written, failed=failed)


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
