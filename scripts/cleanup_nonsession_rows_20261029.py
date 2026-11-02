"""2026-10-29 one-off: delete rows banked on weekends / market holidays.

Before the session gates, weekend runs re-read Friday's vendor values and banked
them as Saturday/Sunday rows (index_skew_daily 10/17, 10/18, 10/24; iv_tenor
10/17, 10/18, 10/24, 10/25; and similar). They duplicate Friday, inflate
percentiles, and for cumulative series (breadth A-D) corrupt the line.

Deletes only rows whose date is NOT a US trading session. Dry run by default.
Run from the repo root on the laptop:
    python scripts\\cleanup_nonsession_rows_20261029.py
    python scripts\\cleanup_nonsession_rows_20261029.py --apply
"""
from __future__ import annotations

import argparse

from sqlalchemy import delete, select

from trading_intel.config import get_settings
from trading_intel.memory.db import make_session_factory
from trading_intel.memory.models import BreadthSnapshot, IndexSkewDaily, IvTenorSnapshot, VixData
from trading_intel.timeutils import is_trading_session

_TABLES = (
    (IndexSkewDaily, IndexSkewDaily.date),
    (IvTenorSnapshot, IvTenorSnapshot.ts),
    (BreadthSnapshot, BreadthSnapshot.ts),
    (VixData, VixData.date),
)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="delete (default: dry run)")
    a = ap.parse_args()
    with make_session_factory(get_settings())() as s:
        for model, col in _TABLES:
            days = sorted({d for (d,) in s.execute(select(col).distinct()) if d is not None})
            bad = [d for d in days if not is_trading_session(d)]
            print(f"{model.__tablename__}: {len(bad)} non-session dates"
                  + (f" ({bad[0]} … {bad[-1]})" if bad else ""))
            if a.apply and bad:
                n = s.execute(delete(model).where(col.in_(bad))).rowcount
                print(f"  deleted {n} rows")
        if a.apply:
            s.commit()
        else:
            print("dry run — re-run with --apply to delete")


if __name__ == "__main__":
    main()
