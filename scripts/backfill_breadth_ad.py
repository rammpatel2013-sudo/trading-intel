"""One-off: backfill the cumulative A-D line history (Norseman N3) from constituent closes.

``breadth_snapshots`` only starts 2026-08-11, so the A-D rail can't measure a long
divergence gap. This rebuilds per-session advancers/decliners from FMP light
history (~1 year) for TODAY's constituents, cumulates it, and splices it onto the
earliest banked row so the line is continuous. Inserts ONLY dates before the first
banked row (ON CONFLICT DO NOTHING) — never touches live rows.

Survivorship caveat: today's membership is applied to every past day, so trust the
line's SHAPE (what the divergence read uses), not its absolute level.

Run (laptop or NAS, once per universe):
    python scripts/backfill_breadth_ad.py            # S&P 500 → source fmp_sp500
    python scripts/backfill_breadth_ad.py --sp600    # S&P 600 → source fmp_sp600
    python scripts/backfill_breadth_ad.py --dry-run
"""
from __future__ import annotations

import argparse

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from trading_intel.clients.cvforge import CVForgeClient
from trading_intel.config import get_settings
from trading_intel.market import breadth as bm
from trading_intel.market import breadth_history as bh
from trading_intel.memory.db import make_session_factory
from trading_intel.memory.models import BreadthSnapshot
from trading_intel.timeutils import is_trading_session


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sp600", action="store_true", help="S&P 600 (IJR holdings) instead of S&P 500")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    st = get_settings()
    source = "fmp_sp600" if a.sp600 else "fmp_sp500"
    cv = CVForgeClient(st)
    syms = bh.etf_holding_symbols(cv, "IJR") if a.sp600 else bm.sp500_symbols(cv)
    print(f"{source}: {len(syms)} constituents — pulling history (takes a few minutes)…")
    dated = bh.fetch_dated_closes(cv, syms)
    daily = [r for r in bh.historical_ad(dated) if is_trading_session(r[0])]
    with make_session_factory(st)() as s:
        first = s.execute(
            select(BreadthSnapshot.ts, BreadthSnapshot.ad_line, BreadthSnapshot.net_adv)
            .where(BreadthSnapshot.source == source, BreadthSnapshot.ad_line.isnot(None))
            .order_by(BreadthSnapshot.ts)
        ).first()
        if first:
            daily = [r for r in daily if r[0] < first[0]]
            # the day before the first banked row must sit at (first.ad_line − first.net_adv)
            anchor = float(first[1]) - float(first[2] or 0)
        else:
            anchor = None
        rows = bh.cumulate_ad(daily, end_value=anchor)
        print(f"{len(rows)} sessions to insert ({rows[0][0] if rows else '-'} → {rows[-1][0] if rows else '-'})"
              f"{'' if first else ' (no banked rows yet — unspliced)'}")
        if a.dry_run or not rows:
            return
        for (d, net, ad), (_, adv, dec) in zip(rows, daily):
            s.execute(pg_insert(BreadthSnapshot).values(
                ts=d, source=source, advancers=adv, decliners=dec, net_adv=net, ad_line=ad,
                n_constituents=len(dated)).on_conflict_do_nothing(constraint="uq_breadth_ts_source"))
        s.commit()
    print("done")


if __name__ == "__main__":
    main()
