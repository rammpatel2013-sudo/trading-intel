"""Scheduled job (EOD): roll up ``tas_prints`` into the durable daily aggregates.

Aggregates each session's raw option-tape prints into ``tas_daily_flow``
(per-name) and ``tas_daily_contract`` (per-contract) so the accumulation /
distribution scorecard survives the 30-day raw-print prune. Reads only our own
``tas_prints`` table — no Convex call — so it takes no ``OptionsDataSource``.

Idempotent: ``INSERT … ON CONFLICT … DO UPDATE`` on the natural key refreshes a
day in place, so re-running (or rolling up a still-filling session) is safe
(CLAUDE.md rule 5). Descriptive flow only — writes nothing to ``signals``
(FlashAlpha rule 4).

By default it rolls up every trade_date present in ``tas_prints`` that is missing
from ``tas_daily_flow``, and always re-rolls the most recent session (it may have
been partial when last rolled). Flags:

Manual run:
    python -m trading_intel.scheduler.jobs.tas_daily_rollup              # catch-up + latest
    python -m trading_intel.scheduler.jobs.tas_daily_rollup --backfill   # every retained day
    python -m trading_intel.scheduler.jobs.tas_daily_rollup --date 2026-06-24
"""

from __future__ import annotations

import argparse
import uuid
from datetime import date

import pandas as pd
import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from trading_intel.config import Settings, get_settings
from trading_intel.flow.aggregate import derive, rollup_by_contract, rollup_by_name
from trading_intel.memory.models import TasDailyContract, TasDailyFlow, TasPrint
from trading_intel.timeutils import eastern_now

log = structlog.get_logger(__name__)

#: Rows per INSERT … ON CONFLICT statement. A single day's contract roll-up can be
#: thousands of rows; one multi-VALUES insert would blow past Postgres's 65,535
#: bound-parameter cap (~14 cols/row), so we chunk well under it.
_INSERT_BATCH = 500

_FLOW_UQ = ["trade_date", "root"]
_FLOW_UPDATE = (
    "prints",
    "total_notional",
    "call_notional",
    "put_notional",
    "buy_notional",
    "sell_notional",
    "net_dollar_delta",
    "gross_dollar_delta",
    "net_premium_call_put",
    "pct_buy",
    "dominant_side",
    "created_at",
)
_CONTRACT_UQ = ["trade_date", "root", "expiry", "strike", "cp"]
_CONTRACT_UPDATE = (
    "n_prints",
    "total_notional",
    "total_size",
    "avg_price",
    "spot",
    "avg_delta",
    "buy_prints",
    "sell_prints",
    "buy_notional",
    "sell_notional",
    "net_dollar_delta",
    "dominant_side",
    "created_at",
)


def _clean(value: object) -> object:
    """numpy/pandas scalar -> plain python; NaN/NaT -> None."""
    if value is None:
        return None
    try:
        if pd.isna(value):  # type: ignore[arg-type]
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        return value.item()
    return value


def _prints_frame(session: Session, day: date) -> pd.DataFrame:
    """Load one session's ``tas_prints`` as a derive-ready DataFrame."""
    rows = list(session.execute(select(TasPrint).where(TasPrint.trade_date == day)).scalars())
    return pd.DataFrame(
        [
            {
                "root": p.root,
                "expiry": p.expiry,
                "strike": p.strike,
                "cp": p.cp,
                "side": p.side,
                "notional": p.notional,
                "size": p.size,
                "price": p.price,
                "delta": p.delta,
                "spot": p.spot,
            }
            for p in rows
        ]
    )


def _dates_to_roll(session: Session, *, target_date: date | None, backfill: bool) -> list[date]:
    """Which sessions to (re)roll: one date, all retained, or catch-up + latest."""
    if target_date is not None:
        return [target_date]
    present = list(
        session.execute(
            select(TasPrint.trade_date).distinct().order_by(TasPrint.trade_date)
        ).scalars()
    )
    if backfill or not present:
        return present
    done = set(session.execute(select(TasDailyFlow.trade_date).distinct()).scalars())
    missing = [d for d in present if d not in done]
    latest = present[-1]
    if latest not in missing:
        missing.append(latest)  # always refresh the most recent (may have been partial)
    return sorted(set(missing))


def _upsert(
    session: Session,
    model: type,
    rows: list[dict],
    uq: list[str],
    update_cols: tuple[str, ...],
) -> None:
    """Chunked idempotent upsert (refresh on the natural key).

    Batches keep each statement under Postgres's 65,535 bound-parameter limit. Each
    batch is also de-duped on the conflict key so a single ``ON CONFLICT DO UPDATE``
    statement never tries to touch the same row twice (Postgres cardinality error).
    """
    if not rows:
        return
    dialect = session.bind.dialect.name if session.bind is not None else "postgresql"
    insert = sqlite_insert if dialect == "sqlite" else pg_insert
    for start in range(0, len(rows), _INSERT_BATCH):
        chunk = rows[start : start + _INSERT_BATCH]
        seen: dict[tuple, dict] = {}
        for r in chunk:
            seen[tuple(r.get(k) for k in uq)] = r  # last write wins within the batch
        stmt = insert(model).values(list(seen.values()))
        stmt = stmt.on_conflict_do_update(
            index_elements=uq,
            set_={c: stmt.excluded[c] for c in update_cols},
        )
        session.execute(stmt)


def rollup_day(session: Session, day: date) -> tuple[int, int]:
    """Aggregate + upsert one session. Returns (name_rows, contract_rows)."""
    now = eastern_now().replace(tzinfo=None)
    df = derive(_prints_frame(session, day))
    if df.empty:
        return 0, 0

    names = rollup_by_name(df)
    contracts = rollup_by_contract(df)

    name_rows = []
    for r in names.to_dict("records"):
        rec = {k: _clean(v) for k, v in r.items()}
        rec["trade_date"] = day
        rec["prints"] = int(rec.get("prints") or 0)
        rec["created_at"] = now
        name_rows.append(rec)

    contract_rows = []
    for r in contracts.to_dict("records"):
        rec = {k: _clean(v) for k, v in r.items()}
        rec["trade_date"] = day
        for ic in ("n_prints", "buy_prints", "sell_prints", "total_size"):
            rec[ic] = int(rec.get(ic) or 0)
        rec["created_at"] = now
        contract_rows.append(rec)

    _upsert(session, TasDailyFlow, name_rows, _FLOW_UQ, _FLOW_UPDATE)
    _upsert(session, TasDailyContract, contract_rows, _CONTRACT_UQ, _CONTRACT_UPDATE)
    return len(name_rows), len(contract_rows)


def run(
    session: Session,
    *,
    settings: Settings | None = None,
    target_date: date | None = None,
    backfill: bool = False,
) -> None:
    """Roll up the relevant session(s) of ``tas_prints`` into the daily aggregates."""
    settings = settings or get_settings()
    correlation_id = uuid.uuid4().hex
    bound = log.bind(correlation_id=correlation_id, job="tas_daily_rollup")

    dates = _dates_to_roll(session, target_date=target_date, backfill=backfill)
    total_names = total_contracts = 0
    for day in dates:
        n, c = rollup_day(session, day)
        total_names += n
        total_contracts += c
    session.commit()

    bound.info(
        "tas_daily_rollup.done",
        sessions=len(dates),
        first=dates[0].isoformat() if dates else None,
        last=dates[-1].isoformat() if dates else None,
        name_rows=total_names,
        contract_rows=total_contracts,
    )


def main() -> None:
    from trading_intel.memory.db import make_session_factory

    p = argparse.ArgumentParser(description="Roll up tas_prints into daily aggregates.")
    p.add_argument("--backfill", action="store_true", help="roll up every retained session")
    p.add_argument("--date", help="roll up a single session YYYY-MM-DD")
    args = p.parse_args()

    settings = get_settings()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    target = date.fromisoformat(args.date) if args.date else None
    session_factory = make_session_factory(settings)
    with session_factory() as session:
        run(session, settings=settings, target_date=target, backfill=args.backfill)


if __name__ == "__main__":
    main()
