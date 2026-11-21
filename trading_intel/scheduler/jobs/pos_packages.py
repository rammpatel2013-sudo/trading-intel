"""pos_packages - created 2026-11-21. SPX spread packages per session -> pos_packages (owner table).

For each session D: reads the SPX/SPXW prints of D from ``tas_prints`` (only prints that belong to a multi-leg
fill - filtered in SQL), builds package events with trading_intel.positions.packages, and classifies each one with
the OI change D -> D+1 from the nightly optionsdata.ai files (reports/optionsdata/<YYYYMMDD>/SPX.csv.gz: the file
written on day X carries OI as of the previous close, so dOI for D = OI(file of the next session) - OI(file of D)).
Until the next session's file exists the action stays "unknown"; every run redoes the last ``--days`` sessions,
so D is settled the following evening. Upsert on (symbol, trade_date, pkg_key) - re-runnable.

Run (NAS, chained in front of the existing DSM task "TI flow bank od" 17:25 - no new task):
    bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_job.sh pos_packages
    python -m trading_intel.scheduler.jobs.pos_packages [--days 5] [--backfill] [--date YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import csv
import gzip
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import structlog
from sqlalchemy import text
from sqlalchemy.orm import Session

from trading_intel.config import get_settings
from trading_intel.positions import packages as P
from trading_intel.timeutils import eastern_now, eastern_today

log = structlog.get_logger(__name__)
ROOT = Path(__file__).resolve().parents[3]
OD_DIR = ROOT / "reports" / "optionsdata"
csv.field_size_limit(10_000_000)

FILL_SQL = """
WITH f AS (
  SELECT date_trunc('second', ts) AS s, size FROM tas_prints
   WHERE trade_date = :d AND root IN ('SPX', 'SPXW') AND (session IS NULL OR session = 'rth') AND size > 0
   GROUP BY 1, 2 HAVING count(DISTINCT (root, expiry, strike, cp)) >= 2)
SELECT p.ts, p.root, p.expiry, p.strike, p.cp, p.side, p.price, p.size, p.quote_loc
  FROM tas_prints p JOIN f ON date_trunc('second', p.ts) = f.s AND p.size = f.size
 WHERE p.trade_date = :d AND p.root IN ('SPX', 'SPXW') AND (p.session IS NULL OR p.session = 'rth')
"""


def load_prints(session: Session, d: date) -> list[dict]:
    out = []
    for r in session.execute(text(FILL_SQL), {"d": d}):
        m = r._mapping
        if m["expiry"] is None or m["strike"] is None or m["cp"] not in ("C", "P"):
            continue
        out.append({"ts": P.to_et(m["ts"], d), "root": m["root"], "expiry": m["expiry"], "strike": float(m["strike"]),
                    "cp": m["cp"], "side": m["side"], "price": m["price"], "size": m["size"], "quote_loc": m["quote_loc"]})
    return out


def od_oi(path: Path) -> dict | None:
    """{(root, expiry, strike, cp): open_interest} from one optionsdata SPX file; None when the file is missing."""
    if not path.is_file():
        return None
    ct, oi = {}, {}
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            rec = r.get("record")
            if rec == "contract":
                try:
                    ct[r["iid"]] = (r["root"], date.fromisoformat(r["expiration"]), float(r["strike"]), r["right"][:1].upper())
                except (KeyError, ValueError):
                    continue
            elif rec == "oi" and r.get("open_interest"):
                oi[r["iid"]] = float(r["open_interest"])
    return {ct[i]: v for i, v in oi.items() if i in ct}


def oi_change(d: date, nxt: date | None, base: Path = OD_DIR) -> dict:
    """dOI for trading on d; empty when either file is missing (actions then stay unknown)."""
    if nxt is None:
        return {}
    a, b = od_oi(base / f"{d:%Y%m%d}" / "SPX.csv.gz"), od_oi(base / f"{nxt:%Y%m%d}" / "SPX.csv.gz")
    if a is None or b is None:
        return {}
    return {k: b.get(k, 0.0) - a.get(k, 0.0) for k in set(a) | set(b)}


def rows_for_day(d: date, prints: list[dict], doi: dict, now: datetime) -> list[dict]:
    rows = []
    for r in P.classify(P.events_for_day(prints), doi):
        e = r["ev"]
        (r1, e1, k1, c1), (r2, e2, k2, _c2) = e.legs
        rows.append({"symbol": "SPX", "trade_date": d, "pkg_key": e.key, "structure": e.structure, "cp": c1,
                     "dear_root": r1, "dear_expiry": e1, "dear_strike": k1, "cheap_root": r2, "cheap_expiry": e2,
                     "cheap_strike": k2, "first_ts": e.first_ts, "last_ts": e.last_ts, "lots": e.size, "fills": e.fills,
                     "net": e.net, "side": e.side, "vote": round(e.vote, 1), "action": r["action"],
                     "oi_score": r["oi_score"], "roll_key": r["roll_key"], "source": "convex_tape", "ingested_at": now})
    return rows


def save(session: Session, rows: list[dict], chunk: int = 1000) -> int:
    from trading_intel.memory.models_positions import PosPackage

    if not rows:
        return 0
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    keep = ("action", "oi_score", "roll_key", "last_ts", "lots", "fills", "net", "side", "vote", "ingested_at")
    for i in range(0, len(rows), chunk):
        stmt = insert(PosPackage).values(rows[i:i + chunk])
        stmt = stmt.on_conflict_do_update(index_elements=["symbol", "trade_date", "pkg_key"],
                                          set_={k: getattr(stmt.excluded, k) for k in keep})
        session.execute(stmt)
    session.commit()
    return len(rows)


def sessions(session: Session, since: date) -> list[date]:
    q = text("SELECT DISTINCT trade_date FROM tas_prints WHERE root = 'SPXW' AND trade_date >= :s ORDER BY 1")
    return [r[0] for r in session.execute(q, {"s": since})]


def run(session: Session, days: int = 5, backfill: bool = False, only: date | None = None, base: Path = OD_DIR) -> dict:
    today = eastern_today()
    all_days = sessions(session, date(2026, 11, 5) if backfill else today - timedelta(days=days * 2 + 7))
    todo = [only] if only else (all_days if backfill else all_days[-days:])
    now = eastern_now()
    out = {}
    for d in todo:
        later = [x for x in all_days if x > d]
        doi = oi_change(d, later[0] if later else None, base)
        rows = rows_for_day(d, load_prints(session, d), doi, now)
        n = save(session, rows)
        known = sum(1 for r in rows if r["action"] != "unknown")
        out[str(d)] = {"packages": n, "classified": known, "oi": bool(doi)}
        log.info("pos_packages.day", day=str(d), packages=n, classified=known, oi=bool(doi))
    try:
        from trading_intel.scheduler import track

        track.report(rows_attempted=sum(v["packages"] for v in out.values()), rows_written=sum(v["packages"] for v in out.values()))
    except Exception:  # noqa: S110 - bookkeeping only
        pass
    return out


def main(argv: list[str] | None = None) -> int:
    from trading_intel.memory.db import make_session_factory

    ap = argparse.ArgumentParser(prog="pos_packages")
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--backfill", action="store_true", help="every SPX session in tas_prints since 2026-11-05")
    ap.add_argument("--date")
    ap.add_argument("--dir", default=str(OD_DIR))
    a = ap.parse_args(argv)
    with make_session_factory(get_settings())() as s:
        res = run(s, a.days, a.backfill, date.fromisoformat(a.date) if a.date else None, Path(a.dir))
    for k, v in res.items():
        print(k, v)
    return 0


if __name__ == "__main__":
    sys.exit(main())
