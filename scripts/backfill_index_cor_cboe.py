"""Backfill index_skew_daily cor10d / cor90d and correct cor1m / cor3m / vixeq / dspx / cboe_skew
to Cboe's official daily closes (created 2026-11-22, Correlation Fuse ship).

Why: the 17:xx index_skew job read COR1M / COR3M from the delayed-quote API, then FMP; since the
delayed-quote API started answering 403 from the NAS (2026-11-20) the banked values drifted from
Cboe's official close (11/20: 37.41 / 31.37 banked vs 36.05 / 29.71). The job now reads the daily
history CSV first (CboeClient.daily_close); this script repairs the rows already banked and fills
the two new columns (migration 0073).

Only touches dates that already have a row (never invents rows). cor10d / cor90d / cboe_skew are
filled where NULL; with --fix the five banked closes are also overwritten when they differ from
Cboe by more than 0.005. Idempotent. Descriptive data only (rule 4).

Run on the NAS (scripts/ is bind-mounted):
    bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_script.sh backfill_index_cor_cboe --fix
    ... --dry-run   (report only)
"""

from __future__ import annotations

import argparse

import structlog
from sqlalchemy import select

from trading_intel.clients.cboe import CboeClient
from trading_intel.config import get_settings
from trading_intel.memory.db import make_session_factory
from trading_intel.memory.models import IndexSkewDaily

log = structlog.get_logger(__name__)

# index_skew_daily column -> Cboe history-CSV name
FILL_ONLY = {"cor10d": "COR10D", "cor90d": "COR90D", "cboe_skew": "SKEW"}
FIXABLE = {"cor1m": "COR1M", "cor3m": "COR3M", "vixeq": "VIXEQ", "dspx": "DSPX"}
TOL = 0.005


def plan(rows, hist: dict, fix: bool) -> list[tuple]:
    """Changes as (date, column, old, new). Pure: rows are objects with .date and the columns."""
    out = []
    for row in rows:
        for col, name in FILL_ONLY.items():
            new = hist.get(name, {}).get(row.date)
            if new is not None and getattr(row, col) is None:
                out.append((row.date, col, None, new))
        for col, name in FIXABLE.items():
            new = hist.get(name, {}).get(row.date)
            old = getattr(row, col)
            if new is None:
                continue
            if old is None or (fix and abs(old - new) > TOL):
                out.append((row.date, col, old, new))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fix", action="store_true", help="also overwrite banked closes that differ from Cboe")
    ap.add_argument("--dry-run", action="store_true", help="report only, do not write")
    args = ap.parse_args()

    cboe = CboeClient(timeout=60.0)
    hist = {}
    for name in list(FILL_ONLY.values()) + list(FIXABLE.values()):
        hist[name] = dict(cboe.index_history(name))
        log.info("backfill.csv_loaded", name=name, rows=len(hist[name]),
                 last=max(hist[name]).isoformat() if hist[name] else None)
        if not hist[name]:
            log.warning("backfill.csv_missing", name=name)

    with make_session_factory(get_settings())() as session:
        rows = list(session.execute(select(IndexSkewDaily).order_by(IndexSkewDaily.date)).scalars())
        changes = plan(rows, hist, args.fix)
        by_date = {r.date: r for r in rows}
        for d, col, old, new in changes:
            setattr(by_date[d], col, new)
        counts = {}
        for _d, col, _o, _n in changes:
            counts[col] = counts.get(col, 0) + 1
        log.info("backfill.summary", rows=len(rows), changes=len(changes), by_column=counts, dry_run=args.dry_run)
        for d, col, old, new in changes[-12:]:
            print(f"  {d} {col}: {old} -> {new}")
        if args.dry_run:
            session.rollback()
        else:
            session.commit()


if __name__ == "__main__":
    main()
