"""Scheduled job (EOD): VIX options chain snapshot -> ``vix_options_chain``.

Pulls the VIX options chain via ``OptionsDataSource.vix_chain`` (which probes
the underlying-symbol convention Convex accepts — ``_VIX`` first, then ``VIX``)
and upserts one row per (ts, expiration, strike, opt_kind). The dashboard reads
this for the VIX-options view; the EOD ``index_skew`` job aggregates it into
``index_skew_daily``.

Idempotent: ``ON CONFLICT (ts, expiration, strike, opt_kind) DO UPDATE``.

Manual run:
    python -m trading_intel.scheduler.jobs.vix_options
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
from trading_intel.errors import DataSourceError
from trading_intel.memory.models import VixOptionsChain
from trading_intel.timeutils import eastern_now

log = structlog.get_logger(__name__)

_UQ_COLS = ["ts", "expiration", "strike", "opt_kind"]
_UPDATE_COLS = ("delta", "iv", "oi", "oi_change", "volume")


def _records_from_chain(chain: pd.DataFrame, *, as_of: date) -> list[dict]:
    """Project the normalized chain into ``vix_options_chain`` row dicts."""
    if chain is None or chain.empty:
        return []
    df = chain.copy()
    if "expiration" not in df.columns or "strike" not in df.columns:
        return []
    df["expiration"] = pd.to_datetime(df["expiration"], errors="coerce")
    df = df.dropna(subset=["expiration", "strike", "opt_kind"])
    if df.empty:
        return []
    df["opt_kind"] = df["opt_kind"].astype(str).str.lower().str[:4]

    out: list[dict] = []
    for r in df.itertuples(index=False):
        out.append(
            {
                "ts": as_of,
                "expiration": r.expiration.date(),
                "strike": float(r.strike),
                "opt_kind": str(r.opt_kind),
                "delta": _as_float(getattr(r, "delta", None)),
                "iv": _as_float(getattr(r, "iv", None)),
                "oi": _as_float(getattr(r, "oi", None)),
                "oi_change": _as_float(getattr(r, "oi_change", None)),
                "volume": _as_float(getattr(r, "volume", None)),
            }
        )
    return out


def _as_float(v: object) -> float | None:
    """Coerce to float; ``None`` if NaN / not coercible."""
    if v is None:
        return None
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def _upsert(session: Session, records: list[dict]) -> None:
    if not records:
        return
    stmt = pg_insert(VixOptionsChain).values(records)
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
) -> None:
    """Pull the EOD VIX chain and upsert into ``vix_options_chain``."""
    settings = settings or get_settings()
    correlation_id = uuid.uuid4().hex
    bound = log.bind(correlation_id=correlation_id, job="vix_options")

    as_of = eastern_now().date()
    try:
        chain = source.vix_chain(exps=(1, 2, 3, 4))
    except DataSourceError as exc:
        bound.warning("vix_options.vix_chain_failed", error=str(exc))
        return

    records = _records_from_chain(chain, as_of=as_of)
    _upsert(session, records)
    session.commit()
    bound.info("vix_options.done", as_of=as_of.isoformat(), rows=len(records))


def main() -> None:
    """Manual entrypoint: build the Convex client + session, run once."""
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
    session_factory = make_session_factory(settings)
    source = ConvexClient(settings)
    with session_factory() as session:
        run(session, source, settings=settings)


if __name__ == "__main__":
    main()
