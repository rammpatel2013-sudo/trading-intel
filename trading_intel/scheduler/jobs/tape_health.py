"""tape_health - created 2026-11-22. Did we capture the SPX tape today? (silent holes cost us 10/19, 10/22,
10/28-11/04, 11/06-11/11 - the tape has no history, a missed day is gone.)

For each session: SPX+SPXW prints and contracts in ``tas_prints``, vs (a) the Convex closing-book day volume in
``oi_chain_eod`` (source convex_eod, when stored) and (b) the median of the last 20 sessions; RTH half-hours with no
prints. status ok / thin (< 50% of median or of chain volume) / hole (an empty RTH half-hour, or < 15%).
Writes tape_coverage_daily (upsert on date+root) and sends a Telegram alert ONLY when status is not ok.

Run (NAS, chained after pos_packages in the existing DSM task "TI flow bank od" 17:25 - no new task):
    python -m trading_intel.scheduler.jobs.tape_health [--days 3]
"""

from __future__ import annotations

import argparse
import statistics
import sys
from datetime import date, datetime, time, timedelta

import structlog
from sqlalchemy import text
from sqlalchemy.orm import Session

from trading_intel.config import get_settings
from trading_intel.positions.packages import to_et
from trading_intel.timeutils import eastern_now, eastern_today

log = structlog.get_logger(__name__)
ROOT_KEY = "SPX*"
THIN, HOLE = 50.0, 15.0
EARLY_CLOSE = {date(2026, 11, 27), date(2026, 12, 24), date(2027, 11, 26)}  # 13:00 ET close


def rth_slots(d: date | None = None) -> list[str]:
    end = time(13, 0) if d in EARLY_CLOSE else time(16, 0)
    out, t = [], datetime(2000, 1, 1, 9, 30)
    while t.time() < end:
        out.append(f"{t:%H:%M}")
        t += timedelta(minutes=30)
    return out


def classify(
    contracts: float, chain_vol: float | None, median: float | None, empty: list[str]
) -> tuple:
    cov = None if not chain_vol else contracts / chain_vol * 100.0
    vsm = None if not median else contracts / median * 100.0
    ref = cov if cov is not None else vsm
    if empty or (ref is not None and ref < HOLE):
        status = "hole"
    elif ref is not None and ref < THIN:
        status = "thin"
    else:
        status = "ok"
    return cov, vsm, status


def day_stats(session: Session, d: date) -> dict:
    q = text("SELECT ts, size FROM tas_prints WHERE trade_date = :d AND root IN ('SPX', 'SPXW')")
    slots = {s: 0 for s in rth_slots(d)}
    n, contracts, first, last = 0, 0.0, None, None
    for ts, size in session.execute(q, {"d": d}):
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        et = to_et(ts, d)
        n += 1
        contracts += float(size or 0)
        hm = f"{et:%H:%M}"
        first = hm if first is None or hm < first else first
        last = hm if last is None or hm > last else last
        slot = f"{et.hour:02d}:{0 if et.minute < 30 else 30:02d}"
        if slot in slots:
            slots[slot] += 1
    return {
        "prints": n,
        "contracts": contracts,
        "first": first,
        "last": last,
        "empty": [s for s, c in slots.items() if c == 0] if n else rth_slots(d),
    }


def chain_volume(session: Session, d: date) -> float | None:
    q = text(
        "SELECT sum(volume) FROM oi_chain_eod WHERE symbol = 'SPX' AND source = 'convex_eod' "
        "AND ts >= :d AND ts < :d1"
    )
    try:
        v = session.execute(q, {"d": d, "d1": d + timedelta(days=1)}).scalar()
    except Exception:
        session.rollback()
        return None
    return float(v) if v else None


def median_before(session: Session, d: date, n: int = 20) -> float | None:
    q = text(
        "SELECT contracts FROM tape_coverage_daily WHERE root = :r AND date < :d AND status = 'ok' "
        "ORDER BY date DESC LIMIT :n"
    )
    vals = [float(r[0]) for r in session.execute(q, {"r": ROOT_KEY, "d": d, "n": n}) if r[0]]
    return statistics.median(vals) if len(vals) >= 3 else None


HOLIDAYS = {
    date(2026, 11, 26),
    date(2026, 12, 25),
    date(2027, 1, 1),
    date(2027, 1, 18),
    date(2027, 2, 15),
    date(2027, 3, 26),
    date(2027, 5, 31),
    date(2027, 6, 18),
    date(2027, 7, 5),
    date(2027, 9, 6),
    date(2027, 11, 25),
    date(2027, 12, 24),
}  # NYSE full closes (2027-12-24 = Christmas observed)


def sessions(since: date, today: date, after_close: bool) -> list[date]:
    """Trading days from ``since`` to today (today only once the session is over). Calendar, not the tape -
    a day with NO prints must still show up as a hole."""
    out, d = [], since
    while d <= today:
        if d.weekday() < 5 and d not in HOLIDAYS and (d < today or after_close):
            out.append(d)
        d += timedelta(days=1)
    return out


def run(session: Session, days: int = 3, telegram: bool = True) -> list[dict]:
    from trading_intel.memory.models_positions import TapeCoverageDaily

    if session.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    out = []
    now = eastern_now()
    for d in sessions(
        eastern_today() - timedelta(days=days * 2 + 5), eastern_today(), now.time() >= time(16, 15)
    )[-days:]:
        st = day_stats(session, d)
        cov, vsm, status = classify(
            st["contracts"], chain_volume(session, d), median_before(session, d), st["empty"]
        )
        row = {
            "date": d,
            "root": ROOT_KEY,
            "prints": st["prints"],
            "contracts": st["contracts"],
            "chain_volume": chain_volume(session, d),
            "coverage_pct": cov,
            "vs_median_pct": vsm,
            "first_et": st["first"],
            "last_et": st["last"],
            "empty_rth_slots": ",".join(st["empty"])[:200] or None,
            "status": status,
            "ingested_at": eastern_now(),
        }
        stmt = insert(TapeCoverageDaily).values(row)
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=["date", "root"],
                set_={k: getattr(stmt.excluded, k) for k in row if k not in ("date", "root")},
            )
        )
        session.commit()
        out.append(row)
        log.info(
            "tape_health.day",
            day=str(d),
            status=status,
            prints=st["prints"],
            coverage=cov,
            vs_median=vsm,
        )
    bad = [r for r in out if r["status"] != "ok"]
    if telegram and bad:
        from trading_intel.clients.telegram import TelegramClient

        msg = ["<b>SPX tape capture problem</b> (a missed tape day can't be recovered)"]
        for r in bad:
            pct = r["coverage_pct"] if r["coverage_pct"] is not None else r["vs_median_pct"]
            msg.append(
                f"• {r['date']:%a %m/%d}: {r['status'].upper()} · {r['prints']:,} prints"
                + (f" · {pct:.0f}% of normal" if pct is not None else "")
                + (f" · empty {r['empty_rth_slots']}" if r["empty_rth_slots"] else "")
            )
        msg.append("Check: sudo tail -50 ~/ti_tas_capture.log (tas capture task).")
        TelegramClient(get_settings()).send_message("\n".join(msg))
    return out


def main(argv: list[str] | None = None) -> int:
    from trading_intel.memory.db import make_session_factory

    ap = argparse.ArgumentParser(prog="tape_health")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--no-telegram", action="store_true")
    a = ap.parse_args(argv)
    with make_session_factory(get_settings())() as s:
        for r in run(s, a.days, telegram=not a.no_telegram):
            print(
                r["date"],
                r["status"],
                r["prints"],
                r["coverage_pct"],
                r["vs_median_pct"],
                r["empty_rth_slots"],
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
