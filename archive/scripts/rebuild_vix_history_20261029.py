"""2026-10-29 one-off (re-runnable): re-date ``vix_data`` from Cboe's daily history.

Until 2026-10-29 ``vix_snapshot`` took VIX from FRED (VIXCLS), which posts a day
late, so every row's ``vix`` (and ``vix_sd20``, ``vega_zone``, ``vrp``) was the
PREVIOUS session's, and HY/IG OAS sat one session late too. This rewrites, for
every row from ``--since``:
  * vix          = Cboe VIX close for that date
  * vix9d/vix3m/vix6m/vvix = Cboe closes — only where the row has none
  * vix_sd20 / vvix_sd20 = trailing-20 stdev from the rebuilt series
  * vega_zone, vrp (VIX − SPX rv20 of that date)
  * hy_oas / ig_oas = FRED observation of THAT date
and inserts rows for sessions that are missing (e.g. 2026-10-21).

Run from the repo root on the laptop (needs DB + FRED key; Cboe CSVs are public):
    python scripts\\rebuild_vix_history_20261029.py              # dry run
    python scripts\\rebuild_vix_history_20261029.py --apply
"""
from __future__ import annotations

import argparse
from datetime import date

import pandas as pd
from sqlalchemy import select

from trading_intel.clients.cboe import CboeClient
from trading_intel.clients.fred import HY_OAS_SERIES, IG_OAS_SERIES, FredClient
from trading_intel.config import get_settings
from trading_intel.dashboard.vix_view import classify_zone
from trading_intel.memory.db import make_session_factory
from trading_intel.memory.models import QuoteDaily, VixData
from trading_intel.scheduler.jobs.vix_snapshot import _dated
from trading_intel.timeutils import eastern_now, is_trading_session


def _sd20(series: dict[date, float], d: date) -> float | None:
    vals = [v for k, v in sorted(series.items()) if k <= d][-20:]
    return float(pd.Series(vals).std(ddof=0)) if len(vals) >= 2 else None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--since", default="2024-06-01")
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    a = ap.parse_args()
    since = date.fromisoformat(a.since)
    today = eastern_now().date()
    st = get_settings()
    cboe = CboeClient(timeout=30.0)
    hist = {n: dict(cboe.index_history(n)) for n in ("VIX", "VIX9D", "VIX3M", "VIX6M", "VVIX")}
    for n, h in hist.items():
        print(f"Cboe {n}: {len(h)} closes, last {max(h) if h else '-'}")
    if not hist["VIX"]:
        raise SystemExit("no Cboe VIX history — nothing written")
    fred = FredClient(st)
    hy, ig = _dated(fred.series(HY_OAS_SERIES)), _dated(fred.series(IG_OAS_SERIES))
    print(f"FRED HY {len(hy)} obs (from {min(hy) if hy else '-'}), IG {len(ig)} obs")

    with make_session_factory(st)() as s:
        rv = {
            r.date: float(r.rv20) for r in s.execute(
                select(QuoteDaily.date, QuoteDaily.rv20)
                .where(QuoteDaily.symbol == "SPX", QuoteDaily.rv20.is_not(None))
            )
        }
        rows = {r.date: r for r in s.execute(select(VixData).where(VixData.date >= since)).scalars()}
        days = sorted(d for d in hist["VIX"] if since <= d <= today and is_trading_session(d))
        changed, inserted = 0, 0
        vvix_series: dict[date, float] = {}
        print("date       | vix banked → Cboe | hy banked → FRED")
        for d in days:
            row = rows.get(d)
            if row is None:
                row = VixData(date=d)
                s.add(row)
                inserted += 1
            old_vix, old_hy = row.vix, row.hy_oas
            row.vix = hist["VIX"][d]
            for col, n in (("vix9d", "VIX9D"), ("vix3m", "VIX3M"), ("vix6m", "VIX6M"), ("vvix", "VVIX")):
                if getattr(row, col) is None and d in hist[n]:
                    setattr(row, col, hist[n][d])
            if row.vvix is not None:
                vvix_series[d] = float(row.vvix)
            row.vix_sd20 = _sd20(hist["VIX"], d)
            row.vvix_sd20 = _sd20(vvix_series, d)
            row.vega_zone = classify_zone(row.vix)
            r20 = max((k for k in rv if k <= d), default=None)
            row.vrp = (row.vix - rv[r20] * 100.0) if r20 is not None else None
            row.hy_oas = hy.get(d, row.hy_oas)
            row.ig_oas = ig.get(d, row.ig_oas)
            if old_vix != row.vix or old_hy != row.hy_oas:
                changed += 1
            if d >= days[-1].replace(day=1) or old_vix is None:
                print(f"{d} | {old_vix} → {row.vix} | {old_hy} → {row.hy_oas}")
        print(f"{len(days)} sessions: {changed} changed, {inserted} inserted")
        if not a.apply:
            s.rollback()
            print("dry run — re-run with --apply to write")
            return
        s.commit()
    print("done")


if __name__ == "__main__":
    main()
