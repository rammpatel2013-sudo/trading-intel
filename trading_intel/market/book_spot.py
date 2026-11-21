"""book_spot.py - created 2026-11-14. The ONE place that picks the spot belonging to an options book.

Why (2026-11-14 audit): ``oi_chain_eod`` stamps every book at 00:00 of its day (a same-day re-run must not
duplicate rows). The 11/12 close-odds fix read that stamp as a clock time ("last spot at or before the book
+ 30 min"), so every END-OF-DAY book was priced at the PREVIOUS session's close (11/13: 7,592 instead of
7,561). Rule: never derive a spot from a book's ``ts`` with a clock offset. Ask this module.

Two kinds of book share the 00:00 stamp:
  - EOD book (source ``convex_eod`` / ``convex_eod_set``, written ~17:15): positions + IVs at that day's
    close -> the last convex spot ON that day.
  - AM book (source ``convex_am`` / ``convex_am_set``, written ~08:35 by index_walls_am, replaced by the EOD
    book that evening): pre-open IVs -> the last convex spot BEFORE that day's 09:30 open (= prior close).
Anything else (old ``convex`` rows) is treated as an EOD book.

2026-11-21: SPX also gets a closing book (``convex_eod`` / ``convex_eod_set``, from the oi_chain_eod SPX full
pull) stamped 16:00 of the day instead of 00:00 - its 08:35 book keeps the 00:00 stamp, so the two never share a
``ts``. Same rule applies: EOD book -> last spot on that day.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.memory.models import GreeksSnapshot, OiChainEod

RTH_OPEN = time(9, 30)


def book_kind(session: Session, symbol: str, ts: datetime) -> str:
    """'eod' or 'am' for the book stamped ``ts`` (EOD wins when both are present)."""
    srcs = set(session.execute(
        select(OiChainEod.source).where(OiChainEod.symbol == symbol.upper(), OiChainEod.ts == ts).distinct()
    ).scalars().all())
    if any(str(s).startswith("convex_am") for s in srcs) and not any(
            str(s).startswith("convex_eod") for s in srcs):
        return "am"
    return "eod"


def _last_spot(session: Session, symbol: str, lo: datetime | None, hi: datetime) -> tuple[float, datetime] | None:
    q = select(GreeksSnapshot.spot, GreeksSnapshot.ts).where(
        GreeksSnapshot.symbol == symbol.upper(), GreeksSnapshot.source == "convex",
        GreeksSnapshot.spot.isnot(None), GreeksSnapshot.ts < hi)
    if lo is not None:
        q = q.where(GreeksSnapshot.ts >= lo)
    row = session.execute(q.order_by(GreeksSnapshot.ts.desc()).limit(1)).first()
    return (float(row[0]), row[1]) if row and row[0] is not None else None


def book_spot(session: Session, symbol: str, ts: datetime) -> dict[str, Any] | None:
    """Spot that belongs to the book stamped ``ts``: ``{"spot", "spot_ts", "kind", "rule", "matches_book"}``.

    ``matches_book`` is False when the right spot was missing (collector gap) and an older one had to be
    used - callers must show that, not hide it."""
    day: date = ts.date()
    kind = book_kind(session, symbol, ts)
    start = datetime.combine(day, time.min)
    if kind == "am":
        got = _last_spot(session, symbol, None, datetime.combine(day, RTH_OPEN))
        rule = "AM book: last spot before that day's 09:30 open (prior close)"
        same = got is not None and got[1].date() < day
    else:
        got = _last_spot(session, symbol, start, start + timedelta(days=1))
        rule = "EOD book: last spot on the book's own day"
        same = got is not None
        if got is None:
            got = _last_spot(session, symbol, None, start)
            rule = "EOD book: NO spot on the book's day - fell back to the last earlier spot"
            same = False
    if got is None:
        return None
    return {"spot": got[0], "spot_ts": got[1].isoformat(timespec="minutes"), "kind": kind, "rule": rule,
            "matches_book": bool(same)}
