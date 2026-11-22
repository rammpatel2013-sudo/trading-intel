"""Background score of the expiry-week decay line -> ``decay_line_marks`` (2026-11-21).

For each session: the 08:35 SPX book (``oi_chain_eod`` convex_am / convex_am_set, OI = prior close),
options expiring from that day through the week's Friday, and the morning ``pos_ledger``. At 10:00,
13:00 and 15:50 Eastern it computes the dealer decay hedge by SPX level for the three variants in
``trading_intel.market.decay_line`` (N naive / W near-money / L signed ledger + near-money), stores
the sign changes nearest spot and the hedge at spot, and scores it: hit = SPX moved from the mark to
the official close the way the hedge pushes (buy -> up, sell -> down).

Nothing is drawn on any cockpit - [stated] 11/21 "test cockpit is getting crowded, score and test
in background". Re-runs the last ``--days`` sessions every night (the official close is corrected
by the next quotes_daily run). Idempotent: a session's rows are replaced. Descriptor only (rule 4).

    python -m trading_intel.scheduler.jobs.decay_line_score            # last 5 sessions
    python -m trading_intel.scheduler.jobs.decay_line_score --since 2026-11-05
    python -m trading_intel.scheduler.jobs.decay_line_score --report   # hit rates so far
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import structlog
from sqlalchemy import Integer, cast, delete, func, select, text
from sqlalchemy.orm import Session

from trading_intel.market import decay_line as DL
from trading_intel.memory.models import OiChainEod, PosLedger, TasPrint
from trading_intel.memory.models_equity import EquityBar
from trading_intel.memory.models_positions import DecayLineMark
from trading_intel.timeutils import eastern_now, eastern_today

log = structlog.get_logger(__name__)

MARKS = ("10:00", "13:00", "15:50")
#: tas_prints.ts was naive UTC up to this day, naive Eastern after (clock fix 2026-11-19 evening)
TAS_UTC_UNTIL = date(2026, 11, 19)
_ET, _UTC = ZoneInfo("America/New_York"), ZoneInfo("UTC")


def _friday(d: date) -> date:
    return d + timedelta(days=(4 - d.weekday()) % 7)


def _book(session: Session, day: date) -> list[tuple]:
    """The 08:35 SPX book (OI = prior close) for expiries day..Friday; the 16:00 closing book is excluded."""
    c = OiChainEod
    return session.execute(
        select(c.expiry, c.strike, c.cp, c.oi, c.iv, c.source).where(
            c.symbol == "SPX", c.ts >= datetime.combine(day, time()), c.ts < datetime.combine(day, time(8, 59)),
            c.source.in_(("convex_am", "convex_am_set")), c.expiry >= day, c.expiry <= _friday(day),
            c.oi > 0, c.iv > 0.02, c.iv < 3,
        )
    ).all()


def _ledger(session: Session, day: date) -> dict[tuple, float]:
    p = PosLedger
    rows = session.execute(
        select(p.expiry, p.strike, p.right, p.pos_raw).where(
            p.as_of_date == day, p.expiry >= day, p.expiry <= _friday(day), p.pos_raw.is_not(None)
        )
    ).all()
    return {(r[0], float(r[1]), r[2]): float(r[3]) for r in rows}


def _prev_close(session: Session, day: date) -> float | None:
    v = session.execute(
        select(EquityBar.close).where(EquityBar.symbol == "SPX", EquityBar.date < day)
        .order_by(EquityBar.date.desc()).limit(1)
    ).scalar()
    return float(v) if v is not None else None


def _close(session: Session, day: date) -> float | None:
    v = session.execute(
        select(EquityBar.close).where(EquityBar.symbol == "SPX", EquityBar.date == day)
    ).scalar()
    return float(v) if v is not None else None


def _spot_at(session: Session, day: date, mark: str) -> float | None:
    """Median SPX spot on the tape within +-3 minutes of the mark."""
    hh, mm = map(int, mark.split(":"))
    t_et = datetime.combine(day, time(hh, mm))
    t = t_et
    if day <= TAS_UTC_UNTIL:
        t = t_et.replace(tzinfo=_ET).astimezone(_UTC).replace(tzinfo=None)
    vals = session.execute(
        select(TasPrint.spot).where(
            TasPrint.trade_date == day, TasPrint.root.in_(("SPX", "SPXW")), TasPrint.spot > 0,
            TasPrint.ts >= t - timedelta(minutes=3), TasPrint.ts <= t + timedelta(minutes=3),
        ).limit(2000)
    ).scalars().all()
    return float(np.median(vals)) if vals else None


def contracts_for(session: Session, day: date) -> list[DL.Contract]:
    led = _ledger(session, day)
    out: list[DL.Contract] = []
    for exp, k, cp, oi, iv, src in _book(session, day):
        am = src == "convex_am_set"
        settles = datetime.combine(exp, time(9, 30) if am else time(16, 0))
        lp = None if am else led.get((exp, float(k), cp))
        out.append(DL.Contract(float(k), cp == "C", float(oi), float(iv), settles, lp))
    return out


def score_day(session: Session, day: date) -> int:
    """Compute and store one session's marks; returns rows written (0 = no book / price)."""
    contracts = contracts_for(session, day)
    ref = _prev_close(session, day)
    if not contracts or ref is None:
        log.warning("decay_line.skip", day=day.isoformat(), contracts=len(contracts), ref=ref)
        return 0
    close = _close(session, day)
    grid = np.arange(round(ref * 0.90 / 5) * 5, round(ref * 1.06 / 5) * 5 + 5, 5.0)
    pos = DL.dealer_positions(contracts, ref)
    rows: list[DecayLineMark] = []
    for mark in MARKS:
        hh, mm = map(int, mark.split(":"))
        now = datetime.combine(day, time(hh, mm))
        spot = _spot_at(session, day, mark)
        for v in DL.VARIANTS:
            curve = DL.decay_curve(contracts, pos[v], grid, now)
            fl = DL.flips(list(grid), list(curve))
            es = DL.value_at(grid, curve, spot) if spot else None
            below = max((f for f in fl if spot and f <= spot), default=None)
            above = min((f for f in fl if spot and f > spot), default=None)
            move = (close - spot) if (close is not None and spot) else None
            hit = None if (es is None or move is None or es == 0 or move == 0) else (es > 0) == (move > 0)
            rows.append(DecayLineMark(
                trade_date=day, mark=mark, variant=v, spot=spot, close=close,
                es_at_spot=round(es, 1) if es is not None else None,
                flip_below=below, flip_above=above,
                flips=",".join(f"{x:g}" for x in DL.nearest(fl, spot or ref, 6))[:200] or None,
                move=round(move, 2) if move is not None else None, hit=hit,
                ingested_at=eastern_now(),
            ))
    session.execute(delete(DecayLineMark).where(DecayLineMark.trade_date == day))
    session.add_all(rows)
    session.commit()
    return len(rows)


def sessions(session: Session, days: int, since: date | None) -> list[date]:
    q = text("SELECT DISTINCT CAST(ts AS DATE) FROM oi_chain_eod WHERE symbol='SPX' "
             "AND source='convex_am' AND ts >= :a AND ts <= :b ORDER BY 1")
    start = since or (eastern_today() - timedelta(days=days * 2 + 4))
    ds = [r[0] if isinstance(r[0], date) else date.fromisoformat(str(r[0])[:10])
          for r in session.execute(q, {"a": datetime.combine(start, time()),
                                       "b": datetime.combine(eastern_today(), time())}).all()]
    return ds if since else ds[-days:]


def report(session: Session) -> dict[str, dict[str, float | int]]:
    """Hit rate per variant (and per mark) over every stored, scored row."""
    out: dict[str, dict[str, float | int]] = {}
    rows = session.execute(
        select(DecayLineMark.variant, DecayLineMark.mark, func.count(), func.sum(cast(DecayLineMark.hit, Integer)))
        .where(DecayLineMark.hit.is_not(None)).group_by(DecayLineMark.variant, DecayLineMark.mark)
    ).all()
    for v, m, n, h in rows:
        out.setdefault(v, {"n": 0, "hits": 0})
        out[v]["n"] += int(n)
        out[v]["hits"] += int(h or 0)
        out[v][m] = round((h or 0) / n, 3) if n else 0.0
    for v in out.values():
        v["hit_rate"] = round(v["hits"] / v["n"], 3) if v["n"] else 0.0
    return out


def run(session: Session, *, days: int = 5, since: date | None = None) -> dict[str, int]:
    done: dict[str, int] = {}
    for d in sessions(session, days, since):
        done[d.isoformat()] = score_day(session, d)
    log.info("decay_line.done", sessions=len(done), rows=sum(done.values()))
    return done


def main(argv: list[str] | None = None) -> int:
    from trading_intel.config import get_settings
    from trading_intel.memory.db import make_session_factory

    ap = argparse.ArgumentParser(prog="decay_line_score")
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--since", help="YYYY-MM-DD backfill start")
    ap.add_argument("--report", action="store_true", help="print hit rates only")
    a = ap.parse_args(argv)
    with make_session_factory(get_settings())() as s:
        if not a.report:
            for k, v in run(s, days=a.days, since=date.fromisoformat(a.since) if a.since else None).items():
                print(k, v)
        for v, r in sorted(report(s).items()):
            print("variant", v, r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
