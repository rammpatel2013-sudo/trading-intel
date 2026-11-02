"""2026-10-29 one-off (re-runnable): rebuild ``breadth_snapshots`` by DATE from mid-2024.

Fixes the stale-dated live rows (the 16:30 job filed the previous session's breadth
under today), the McClellan that read the oldest 80 rows, weekend-walked A-D, and
the empty S&P 600 rail. Recomputes every row from one dated history pull with the
live job's own functions (``trading_intel.market.breadth_rebuild``) and upserts
ALL columns for the universe. Safe to re-run; also the way to fill a day the live
job skipped as ``source_stale``.

Needs: DB (.env), FMP (direct key preferred, else the CVForge passthrough).
Run from the repo root on the laptop (C:\\Users\\drmit\\PycharmProjects\\trading-intel):
    python scripts\\rebuild_breadth_history_20261029.py                 # S&P 500, dry run
    python scripts\\rebuild_breadth_history_20261029.py --apply
    python scripts\\rebuild_breadth_history_20261029.py --sp600 --apply
Survivorship caveat: today's membership is applied to every past day.
"""
from __future__ import annotations

import argparse
from datetime import date

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from trading_intel.config import get_settings
from trading_intel.market import breadth as bm
from trading_intel.market import breadth_history as bh
from trading_intel.market import breadth_rebuild as br
from trading_intel.memory.db import make_session_factory
from trading_intel.memory.models import BreadthSnapshot
from trading_intel.timeutils import eastern_now, is_trading_session


def _client(st):
    direct = bh.direct_fmp()
    if direct is not None:
        print("FMP: direct key")
        return direct
    from trading_intel.clients.cvforge import CVForgeClient

    print("FMP: CVForge passthrough")
    return CVForgeClient(st)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sp600", action="store_true", help="S&P 600 (IJR holdings) A-D rail")
    ap.add_argument("--start", default="2024-06-03", help="first rebuilt session")
    ap.add_argument("--warmup-from", default="2023-06-01", help="history pulled from (200-DMA warm-up)")
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    a = ap.parse_args()
    st = get_settings()
    start, warm = date.fromisoformat(a.start), date.fromisoformat(a.warmup_from)
    source = "fmp_sp600" if a.sp600 else "fmp_sp500"
    full = not a.sp600
    cl = _client(st)
    syms = bh.holdings_any(cl, "IJR") if a.sp600 else bm.sp500_symbols(cl)
    print(f"{source}: {len(syms)} constituents")
    if len(syms) < 200:
        raise SystemExit("constituent list too thin — check the FMP key / endpoint (nothing written)")
    print("pulling dated history (a few minutes)…")
    dated = bh.fetch_dated_closes(cl, syms, start=warm)
    print(f"history for {len(dated)} symbols")
    today = eastern_now().date()
    all_days = sorted({d for s in dated.values() for d in s})
    days = [d for d in all_days if start <= d <= today and is_trading_session(d)]
    if not days:
        raise SystemExit("no sessions in range (nothing written)")
    counts = br.per_day_counts(dated, days, full=full)
    spx = []
    with make_session_factory(st)() as s:
        if full:
            from trading_intel.strategies.norseman_regime import spx_bars

            spx = [(b.d, b.high, b.close) for b in spx_bars(s, as_of=today)]
        rows = br.assemble(counts, spx, full=full)
        print(f"{len(rows)} sessions {rows[0]['ts']} → {rows[-1]['ts']}")
        tail = rows[-6:]
        live = {
            r.ts: r for r in s.execute(
                select(BreadthSnapshot).where(BreadthSnapshot.source == source,
                                              BreadthSnapshot.ts >= tail[0]["ts"])
            ).scalars()
        }
        print("last sessions: date | adv/dec rebuilt | adv/dec banked | %>200 rebuilt/banked | spx")
        for r in tail:
            o = live.get(r["ts"])
            print(f"  {r['ts']} | {r['advancers']}/{r['decliners']} | "
                  f"{(o.advancers, o.decliners) if o else '-'} | "
                  f"{r.get('pct_above_200')}/{o.pct_above_200 if o else '-'} | {r.get('spx_close')}")
        if not a.apply:
            print("dry run — re-run with --apply to write")
            return
        cols = [c for c in rows[0] if c != "ts"]
        for r in rows:
            values = {**r, "source": source}
            stmt = pg_insert(BreadthSnapshot).values(values)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_breadth_ts_source", set_={c: stmt.excluded[c] for c in cols}
            )
            s.execute(stmt)
        s.commit()
    print(f"done: {len(rows)} rows upserted for {source}")


if __name__ == "__main__":
    main()
