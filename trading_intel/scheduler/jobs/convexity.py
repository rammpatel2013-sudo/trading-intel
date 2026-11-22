"""Convexity scanner job (2026-11-22). NAS: chained on the "TI compass" task (Mon-Fri 18:50) after compass:
``run_job.sh compass; run_job.sh convexity; run_script.sh convexity_page; run_script.sh risk_page``.

1. latest compass_daily date <= today (IV30, IV rank, carry, term, skew, $ premium per name)
2. each name's daily code, weekly stage and Mansfield RS from equity_stages (latest date <= that day)
3. next earnings date per name from earnings_events (names reporting within 25 days are left out)
4. the four lists (equity/convexity.py) -> equity_convex_picks (idempotent upsert on date + symbol)
5. paper track: every stored list from the last ~70 sessions scored at 5 / 10 / 20 sessions on equity_bars
6. ``scripts/cvxmap/data/convexity.json`` (lists, cross-asset ETF vol board, track record) for the
   Convexity page (scripts/convexity_page.py) and the Risk page card

Descriptor only (rule 4) - nothing here writes ``signals``.
Manual: ``python -m trading_intel.scheduler.jobs.convexity [--day YYYY-MM-DD]``.
"""

from __future__ import annotations

import argparse
import json
import uuid
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from trading_intel.config import Settings, get_settings
from trading_intel.equity import convexity as cx
from trading_intel.equity import store
from trading_intel.memory.models import EarningsEvent
from trading_intel.memory.models_compass import COMPASS_FLOATS, CompassDaily
from trading_intel.memory.models_equity import EquityBar, EquityConvexPick, EquityStage
from trading_intel.timeutils import eastern_now, is_trading_session

log = structlog.get_logger(__name__)
COCKPIT_DATA = Path(__file__).resolve().parents[3] / "scripts" / "cvxmap" / "data"
OUT = "convexity.json"
TRACK_DAYS = 120          # calendar days of stored lists to score (~70 sessions + the 20-session horizon)
PICK_COLS = ("bucket", "rank", "score", "spot", "iv30", "iv_rank", "hv10", "carry", "ts_30_90", "skew25",
             "skew_rank", "code", "wstage", "mrs", "move20")


def _compass(session: Session, day: date) -> tuple[date | None, list[dict]]:
    d = session.execute(select(func.max(CompassDaily.date)).where(CompassDaily.date <= day)).scalar()
    if d is None:
        return None, []
    cols = [CompassDaily.symbol, CompassDaily.grp, CompassDaily.iv_src] + [getattr(CompassDaily, c) for c in COMPASS_FLOATS]
    names = ["symbol", "grp", "iv_src", *COMPASS_FLOATS]
    return d, [dict(zip(names, r, strict=True)) for r in session.execute(select(*cols).where(CompassDaily.date == d))]


def _stages(session: Session, day: date) -> tuple[date | None, dict[str, dict]]:
    d = session.execute(select(func.max(EquityStage.date)).where(EquityStage.date <= day)).scalar()
    if d is None:
        return None, {}
    q = select(EquityStage.symbol, EquityStage.code, EquityStage.stage, EquityStage.wstage, EquityStage.mrs) \
        .where(EquityStage.date == d)
    return d, {s: {"code": c, "stage": st, "wstage": w, "mrs": m} for s, c, st, w, m in session.execute(q)}


def _earnings(session: Session, day: date) -> dict[str, date]:
    q = select(EarningsEvent.symbol, func.min(EarningsEvent.date)).where(
        EarningsEvent.date >= day, EarningsEvent.date <= day + timedelta(days=cx.EARN_DAYS)).group_by(EarningsEvent.symbol)
    return {s: d for s, d in session.execute(q)}


def _track(session: Session, day: date) -> dict:
    since = day - timedelta(days=TRACK_DAYS)
    picks = [{"date": d, "symbol": s, "bucket": b, "spot": sp, "iv30": iv}
             for d, s, b, sp, iv in session.execute(
                 select(EquityConvexPick.date, EquityConvexPick.symbol, EquityConvexPick.bucket, EquityConvexPick.spot,
                        EquityConvexPick.iv30).where(EquityConvexPick.date >= since, EquityConvexPick.date < day))]
    syms = sorted({p["symbol"] for p in picks})
    bars: dict[str, list] = defaultdict(list)
    for i in range(0, len(syms), 500):
        for s, d, c, h, lo in session.execute(
                select(EquityBar.symbol, EquityBar.date, EquityBar.close, EquityBar.high, EquityBar.low)
                .where(EquityBar.symbol.in_(syms[i:i + 500]), EquityBar.date > since, EquityBar.date <= day)
                .order_by(EquityBar.symbol, EquityBar.date)):
            bars[s].append((d, c, h, lo))
    out = cx.track(picks, bars)
    out["lists"] = len({p["date"] for p in picks})
    return out


def run(session: Session, settings: Settings | None = None, day: date | None = None, force: bool = False,
        out_dir: Path | None = None) -> dict:
    settings = settings or get_settings()
    day = day or eastern_now().date()
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="convexity")
    if not force and not is_trading_session(day):
        return {"skipped": "not_a_trading_session"}
    cdate, rows = _compass(session, day)
    if not rows:
        return {"skipped": "no_compass"}
    sdate, st = _stages(session, cdate)
    merged = [{**r, **st.get(r["symbol"], {})} for r in rows]
    picks, counts = cx.pick(merged, cdate, _earnings(session, cdate))
    now = eastern_now().replace(tzinfo=None)
    db_rows = [{"date": cdate, "symbol": p["symbol"], "computed_at": now, **{k: p.get(k) for k in PICK_COLS}}
               for p in picks]
    session.execute(delete(EquityConvexPick).where(EquityConvexPick.date == cdate))   # re-run = replace that day's lists
    n = store.upsert(session, EquityConvexPick, db_rows, ["date", "symbol"], chunk=500)
    session.commit()
    try:
        trk = _track(session, cdate)
    except Exception as exc:  # noqa: BLE001 - the track record is context; today's lists still publish
        session.rollback()
        bound.warning("convexity.track_failed", error=str(exc)[:200])
        trk = {"rows": [], "error": str(exc)[:200]}
    o = {"asof": cdate.isoformat(), "asof_stage": sdate.isoformat() if sdate else None,
         "built": eastern_now().isoformat(timespec="seconds"), "counts": counts,
         "rules": {"earn_days": cx.EARN_DAYS, "min_prem": cx.MIN_PREM, "top_n": cx.TOP_N},
         "picks": {b: [p for p in picks if p["bucket"] == b] for b in cx.BUCKETS},
         "board": cx.vol_board(rows), "track": trk}
    out = (out_dir or COCKPIT_DATA) / OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(o, separators=(",", ":"), default=str), encoding="utf-8")
    tmp.replace(out)
    bound.info("convexity.done", asof=o["asof"], asof_stage=o["asof_stage"], rows=n,
               **{b: counts[b] for b in cx.BUCKETS})
    try:
        from trading_intel.scheduler import track

        track.report(rows_attempted=len(rows), rows_written=n)
    except Exception:  # noqa: S110 - bookkeeping only
        pass
    return {"asof": o["asof"], "asof_stage": o["asof_stage"], "rows": n, "counts": counts}


def main(argv: list[str] | None = None) -> int:
    from trading_intel.memory.db import make_session_factory

    ap = argparse.ArgumentParser(prog="convexity")
    ap.add_argument("--day")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    s = get_settings()
    with make_session_factory(s)() as session:
        res = run(session, s, date.fromisoformat(a.day) if a.day else None, a.force)
    print(json.dumps(res, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
