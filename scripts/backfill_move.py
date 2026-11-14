"""One-time MOVE (ICE BofA bond-vol index) history fill for ``vix_data.move`` (2026-11-14).

Why: ``vix_snapshot`` started writing MOVE from the CVForge FMP passthrough
(``^MOVE``) on 2026-11-13, so every earlier ``vix_data`` row has ``move`` NULL
and ``get_vix`` showed MOVE as null on all rows. This fills those NULL cells
from the same source, so MOVE gets a percentile / 20-day change like VIX.

Idempotent: only NULL ``move`` cells on EXISTING ``vix_data`` rows are filled;
never inserts rows, never overwrites a value. Descriptive data only (rule 4).

Run on the machine with NAS DB access + the CVForge key (the laptop), from the
project root:
    python scripts/backfill_move.py --dry-run
    python scripts/backfill_move.py
    python scripts/backfill_move.py --since 2015-01-01
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta

import structlog
from sqlalchemy import select

from trading_intel.clients.cvforge import CVForgeClient
from trading_intel.config import get_settings
from trading_intel.memory.db import make_session_factory
from trading_intel.memory.models import VixData

log = structlog.get_logger(__name__)

SYMBOL = "^MOVE"
CHUNK_DAYS = 365


def parse_rows(data: object) -> dict[date, float]:
    """FMP ``historical-price-eod/light`` rows -> ``{date: close}`` (price, else close)."""
    out: dict[date, float] = {}
    for r in data if isinstance(data, list) else []:
        if not isinstance(r, dict):
            continue
        v = r.get("price", r.get("close"))
        try:
            d = date.fromisoformat(str(r.get("date", ""))[:10])
            f = float(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if f > 0:
            out[d] = f
    return out


def fetch_move(cvf: CVForgeClient, since: date, until: date) -> dict[date, float]:
    """Pull ^MOVE closes in yearly chunks (keeps each FMP response small)."""
    closes: dict[date, float] = {}
    start = since
    while start <= until:
        end = min(until, start + timedelta(days=CHUNK_DAYS - 1))
        data = cvf.fmp(
            "historical-price-eod/light",
            {"symbol": SYMBOL, "from": start.isoformat(), "to": end.isoformat()},
        )
        got = parse_rows(data)
        log.info("backfill_move.chunk", start=start.isoformat(), end=end.isoformat(), rows=len(got))
        closes.update(got)
        start = end + timedelta(days=1)
    return closes


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--since", default="2021-01-01", help="first date to fill (YYYY-MM-DD)")
    ap.add_argument("--dry-run", action="store_true", help="count only, write nothing")
    args = ap.parse_args()
    since = date.fromisoformat(args.since)
    until = date.today()

    settings = get_settings()
    cvf = CVForgeClient(settings)
    closes = fetch_move(cvf, since, until)
    if not closes:
        print(f"[FAIL] FMP returned no {SYMBOL} rows for {since}..{until} - nothing filled")
        raise SystemExit(1)

    filled = missing_src = 0
    with make_session_factory(settings)() as session:
        rows = session.execute(
            select(VixData).where(VixData.date >= since, VixData.move.is_(None))
        ).scalars().all()
        for row in rows:
            v = closes.get(row.date)
            if v is None:
                missing_src += 1
                continue
            filled += 1
            if not args.dry_run:
                row.move = v
        if not args.dry_run:
            session.commit()
        latest = session.execute(
            select(VixData.date, VixData.move).where(VixData.move.is_not(None))
            .order_by(VixData.date.desc()).limit(1)
        ).first()

    mode = "DRY RUN - would fill" if args.dry_run else "filled"
    print(f"[OK] {SYMBOL}: {len(closes)} closes from FMP; {mode} {filled} vix_data rows; "
          f"{missing_src} NULL rows had no FMP close")
    if latest is not None and not args.dry_run:
        print(f"     latest MOVE in vix_data: {latest.date} = {latest.move}")


if __name__ == "__main__":
    main()
