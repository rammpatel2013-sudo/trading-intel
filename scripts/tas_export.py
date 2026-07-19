"""Export stored ``tas_prints`` (NAS DB) to a capture-format CSV for the reports.

The market-wide tape lives in ``tas_prints`` (the NAS ``tas_capture_job`` fills it
every minute during RTH — full, current, decoded + Greek-annotated). But the report
generators ``tas_analyze.py`` (Excel) and ``tas_signals.py`` (HTML) were written
against the Phase-1 daily CSVs from ``tas_capture.py``, which stopped on 2026-06-03.
This bridges the gap: it pulls prints straight from the DB and writes a CSV in the
exact shape those loaders expect, so you can report on ANY captured day(s) without
touching the (tested) report code.

Both loaders re-decode root/expiry/strike/cp from the raw ``symbol`` column, so we
deliberately emit only the raw capture columns and let them derive the rest.

Read-only on the DB (rule 1: Convex stays in clients/; this only reads our own
table). Descriptive flow only — no signals (rule 4).

Run (repo root, venv active), then feed the printed follow-on commands:
    python scripts/tas_export.py --all               # EVERY retained session (~30d) -> one CSV
    python scripts/tas_export.py --all --run         # ...and build BOTH reports
    python scripts/tas_export.py --days 5            # last 5 captured sessions -> one CSV
    python scripts/tas_export.py --date 2026-06-24   # a single session
    python scripts/tas_export.py --start 2026-06-18 --end 2026-06-24
"""

from __future__ import annotations

import argparse
import subprocess
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.config import get_settings
from trading_intel.memory.db import make_session_factory
from trading_intel.memory.models import TasPrint

log = structlog.get_logger(__name__)
_ET = ZoneInfo("America/New_York")

# Raw capture columns the report loaders read (they decode the contract themselves).
_EXPORT_COLS = [
    "time",
    "symbol",
    "price",
    "size",
    "notional",
    "side",
    "spot",
    "delta",
    "gamma",
    "vega",
    "theta",
    "volatility",
]


def _recent_trade_dates(session: Session, days: int) -> list[date]:
    """The most recent ``days`` distinct trade_dates present in tas_prints."""
    rows = (
        session.execute(
            select(TasPrint.trade_date).distinct().order_by(TasPrint.trade_date.desc()).limit(days)
        )
        .scalars()
        .all()
    )
    return sorted(rows)


def _all_trade_dates(session: Session) -> list[date]:
    """Every distinct trade_date retained in tas_prints (rolling window, ~30d)."""
    rows = (
        session.execute(select(TasPrint.trade_date).distinct().order_by(TasPrint.trade_date))
        .scalars()
        .all()
    )
    return list(rows)


def _resolve_dates(session: Session, args: argparse.Namespace) -> list[date]:
    if args.all:
        return _all_trade_dates(session)
    if args.date:
        return [date.fromisoformat(args.date)]
    if args.start or args.end:
        lo = date.fromisoformat(args.start) if args.start else date(2000, 1, 1)
        hi = date.fromisoformat(args.end) if args.end else datetime.now(_ET).date()
        rows = (
            session.execute(
                select(TasPrint.trade_date)
                .where(TasPrint.trade_date >= lo, TasPrint.trade_date <= hi)
                .distinct()
                .order_by(TasPrint.trade_date)
            )
            .scalars()
            .all()
        )
        return list(rows)
    return _recent_trade_dates(session, args.days)


def export(session: Session, trade_dates: list[date]) -> pd.DataFrame:
    """Fetch tas_prints for ``trade_dates`` as a capture-format DataFrame (ts-ordered)."""
    if not trade_dates:
        return pd.DataFrame(columns=_EXPORT_COLS)
    rows = list(
        session.execute(
            select(TasPrint).where(TasPrint.trade_date.in_(trade_dates)).order_by(TasPrint.ts)
        ).scalars()
    )
    records = [
        {
            "time": p.ts.isoformat() if p.ts is not None else None,
            "symbol": p.symbol,  # raw contract, e.g. .NVDA260619C230
            "price": p.price,
            "size": p.size,
            "notional": p.notional,
            "side": p.side,
            "spot": p.spot,
            "delta": p.delta,
            "gamma": p.gamma,
            "vega": p.vega,
            "theta": p.theta,
            "volatility": None,  # not stored per-print; loaders treat as optional
        }
        for p in rows
    ]
    return pd.DataFrame.from_records(records, columns=_EXPORT_COLS)


def main() -> None:
    p = argparse.ArgumentParser(description="Export stored tas_prints to a report-ready CSV.")
    p.add_argument(
        "--all",
        action="store_true",
        help="report on ALL retained sessions in tas_prints (the full ~30d window)",
    )
    p.add_argument("--days", type=int, default=5, help="last N captured sessions (default 5)")
    p.add_argument("--date", help="single session YYYY-MM-DD (overrides --days)")
    p.add_argument("--start", help="range start YYYY-MM-DD (with --end; overrides --days)")
    p.add_argument("--end", help="range end YYYY-MM-DD")
    p.add_argument("--out-dir", default="data/tas", help="output folder")
    p.add_argument(
        "--run",
        action="store_true",
        help="after export, also run tas_analyze + tas_signals on the CSV",
    )
    args = p.parse_args()

    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    session_factory = make_session_factory(get_settings())
    with session_factory() as session:
        dates = _resolve_dates(session, args)
        if not dates:
            raise SystemExit("no tas_prints rows for the requested period.")
        df = export(session, dates)

    if df.empty:
        raise SystemExit("query returned no prints — check the dates.")

    start, end = dates[0].isoformat(), dates[-1].isoformat()
    name = f"{start}.csv" if start == end else f"rollup_{start}_to_{end}.csv"
    out_path = out_dir / name
    df.to_csv(out_path, index=False)
    n_sym = df["symbol"].nunique()
    log.info("tas_export.done", file=str(out_path), prints=len(df), sessions=len(dates))
    print(
        f"wrote {out_path}  ({len(df):,} prints across {len(dates)} session(s), {n_sym} contracts)"
    )

    analyze = f"python scripts/tas_analyze.py --file {out_path}"
    signals = f"python scripts/tas_signals.py --file {out_path}"
    if not args.run:
        print("\nNow build the reports:")
        print(f"  {analyze}")
        print(f"  {signals}")
        return

    for cmd in (analyze, signals):
        print(f"\n$ {cmd}")
        subprocess.run(cmd.split(), check=False)  # noqa: S603 - fixed, local commands


if __name__ == "__main__":
    main()
