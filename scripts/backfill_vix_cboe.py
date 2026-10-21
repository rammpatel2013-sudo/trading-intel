"""Backfill VVIX + VIX term structure (vix9d/vix3m/vix6m) into ``vix_data``.

Why this exists
---------------
CBOE moved its delayed-quote CDN from ``cdn.cboe.com`` to ``cdn-api.cboe.com``
(the old host now 307-redirects). ``httpx`` does not follow redirects by
default, so ``CboeClient`` silently got the 307 HTML, failed to parse it, and
degraded every CBOE field to ``None`` from 2026-09-24 onward. ``clients/cboe.py``
is now fixed (new host + ``follow_redirects=True``), which repairs the feed
going forward — but the rows already written in the gap still have NULL vvix /
vix9d / vix3m / vix6m. This script fills them from CBOE's public daily history
CSVs (same new host), then recomputes ``vvix_sd20`` over the repaired series.

Safe + idempotent: it only fills rows whose ``vvix`` is currently NULL (never
clobbers good history), touches only dates that already exist in ``vix_data``,
and can be re-run. Descriptive data only (CLAUDE.md rule 4).

Run on the machine with NAS DB access (the laptop), from the project root:
    python scripts/backfill_vix_cboe.py            # fill all NULL-vvix rows
    python scripts/backfill_vix_cboe.py --dry-run  # show what it would do
"""

from __future__ import annotations

import argparse
from datetime import date, datetime

import httpx
import pandas as pd
import structlog
from sqlalchemy import select

from trading_intel.config import get_settings
from trading_intel.memory.db import make_session_factory
from trading_intel.memory.models import VixData

log = structlog.get_logger(__name__)

_CSV = "https://cdn-api.cboe.com/api/global/us_indices/daily_prices/{name}_History.csv"

# vix_data column -> CBOE history-CSV index name
_FIELDS = {"vvix": "VVIX", "vix9d": "VIX9D", "vix3m": "VIX3M", "vix6m": "VIX6M"}


def _fetch_history(name: str) -> dict[date, float]:
    """Parse one CBOE ``{name}_History.csv`` into ``{date: close}``.

    Handles both the 2-column (DATE,CLOSE — e.g. VVIX) and 5-column
    (DATE,OPEN,HIGH,LOW,CLOSE) layouts: CLOSE is always the last field.
    """
    url = _CSV.format(name=name)
    resp = httpx.get(url, timeout=20.0, follow_redirects=True)
    resp.raise_for_status()
    out: dict[date, float] = {}
    for line in resp.text.splitlines()[1:]:  # skip header
        parts = [p.strip() for p in line.split(",") if p.strip() != ""]
        if len(parts) < 2:
            continue
        try:
            d = datetime.strptime(parts[0], "%m/%d/%Y").date()
            close = float(parts[-1])
        except (ValueError, IndexError):
            continue
        out[d] = close
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="report only, do not write")
    args = ap.parse_args()

    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )

    hist = {col: _fetch_history(name) for col, name in _FIELDS.items()}
    for col, name in _FIELDS.items():
        last = max(hist[col]) if hist[col] else None
        log.info("backfill.csv_loaded", field=col, name=name, rows=len(hist[col]),
                 last_date=last.isoformat() if last else None)

    settings = get_settings()
    with make_session_factory(settings)() as session:
        rows = list(
            session.execute(
                select(VixData).where(VixData.vvix.is_(None)).order_by(VixData.date)
            ).scalars()
        )
        if not rows:
            log.info("backfill.nothing_to_do", reason="no rows with NULL vvix")
            return

        filled: list[str] = []
        for row in rows:
            got = {}
            for col in _FIELDS:
                v = hist[col].get(row.date)
                if v is not None:
                    got[col] = v
            if not got:
                continue  # CBOE has no history for this date (e.g. a non-trading day)
            for col, v in got.items():
                setattr(row, col, v)
            filled.append(f"{row.date.isoformat()} " +
                          " ".join(f"{c}={got[c]:g}" for c in sorted(got)))

        first_filled = rows[0].date if filled else None

        # Recompute vvix_sd20 over the now-repaired series (trailing 20 samples,
        # population stdev — matches vix_snapshot.run). Only rows from the first
        # filled date onward can have changed.
        sd_updates = 0
        if first_filled is not None:
            allrows = list(
                session.execute(
                    select(VixData).order_by(VixData.date)
                ).scalars()
            )
            vvix_series = [(r.date, r.vvix) for r in allrows]
            by_date = {r.date: r for r in allrows}
            for i, (d, _v) in enumerate(vvix_series):
                if d < first_filled:
                    continue
                window = [v for _, v in vvix_series[max(0, i - 19): i + 1] if v is not None]
                new_sd = float(pd.Series(window).std(ddof=0)) if len(window) >= 2 else None
                if by_date[d].vvix_sd20 != new_sd:
                    by_date[d].vvix_sd20 = new_sd
                    sd_updates += 1

        log.info("backfill.summary", rows_scanned=len(rows), rows_filled=len(filled),
                 vvix_sd20_updated=sd_updates, dry_run=args.dry_run)
        for line in filled:
            print("  filled", line)

        if args.dry_run:
            session.rollback()
            log.info("backfill.dry_run_rolled_back")
        else:
            session.commit()
            log.info("backfill.committed", rows_filled=len(filled))


if __name__ == "__main__":
    main()
