"""Focus names (2026-11-20): Mithil's up-to-4 single names a trading day.

Mithil 2026-11-20: "lets say tomorrow i want to trade nvda. then old data provide me some level and idea
about swing, then it start collecting 0-7 expiry data with live volume feed from convexvalue. i can select
any equity and it start collecting and provide me same calculation and visualization as index ... allow max
4 equity selection" -> "cockpit box but ability to change after day is over. so its day to day add".

The day rule (one place, used by the cockpit box, the collector and the writer):
  * a pick is for ONE trading day (focus_names.day);
  * that day's list can be changed until 09:30 ET on that day, then it is fixed for the session;
  * during the session and after the close the box edits the NEXT trading day's list;
  * nothing carries over by itself: the box offers "keep today's" for the next day.

Owner table focus_names (schema contract); this module is the only writer. Descriptors only (rule 4).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date, datetime, time

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from trading_intel.chain_bank import FOCUS_SOURCE  # noqa: F401 - re-exported for the writer
from trading_intel.tape_sessions import next_trading_day
from trading_intel.timeutils import eastern_now, is_trading_session

MAX_PICKS = 4
OPEN = time(9, 30)
# already on the cockpit as the index book; a focus pull of them would only spend Convex budget
ON_COCKPIT = {"SPX", "SPXW", "SPY", "QQQ", "VIX", "NDX", "XSP"}
_SYM = re.compile(r"^[A-Z][A-Z0-9.]{0,7}$")


class FocusError(ValueError):
    """A pick list the box must refuse (shown to Mithil as is)."""


def clean(symbols: Iterable[str]) -> list[str]:
    """Upper-case, strip, de-duplicate (first wins), check the spelling. Raises FocusError."""
    out: list[str] = []
    for raw in symbols:
        s = str(raw or "").strip().upper().lstrip("$")
        if not s:
            continue
        if not _SYM.match(s):
            raise FocusError(f"'{raw}' is not a ticker")
        if s in ON_COCKPIT:
            raise FocusError(f"{s} is already on the cockpit")
        if s not in out:
            out.append(s)
    if len(out) > MAX_PICKS:
        raise FocusError(f"at most {MAX_PICKS} focus names a day ({len(out)} given)")
    return out


def session_day(now: datetime) -> date | None:
    """The trading day whose session ``now`` is in or before (None on a closed day)."""
    return now.date() if is_trading_session(now.date()) else None


def edit_day(now: datetime) -> date:
    """The day the cockpit box edits at ``now``: today before 09:30 on a trading day, else the next trading day."""
    d = now.date()
    if is_trading_session(d) and now.time() < OPEN:
        return d
    return next_trading_day(d)


def locked(day: date, now: datetime) -> bool:
    """True when ``day``'s list can no longer change (its session has opened, or it is in the past)."""
    return day < now.date() or (day == now.date() and now.time() >= OPEN)


def get_picks(session: Session, day: date) -> list[str]:
    from trading_intel.memory.models import FocusName

    rows = session.execute(select(FocusName.symbol).where(FocusName.day == day).order_by(FocusName.slot)).scalars()
    return list(rows)


def set_picks(session: Session, day: date, symbols: Iterable[str], now: datetime | None = None,
              source: str = "cockpit") -> list[str]:
    """Replace ``day``'s list (empty list = no focus names that day). Refuses a locked day. Commits."""
    from trading_intel.memory.models import FocusName

    now = now or eastern_now()
    if locked(day, now):
        raise FocusError(f"{day:%a %m/%d} is fixed (its session has opened); edit {edit_day(now):%a %m/%d} instead")
    if not is_trading_session(day):
        raise FocusError(f"{day:%a %m/%d} is not a trading day")
    picks = clean(symbols)
    session.execute(delete(FocusName).where(FocusName.day == day))
    for i, s in enumerate(picks, start=1):
        session.add(FocusName(day=day, slot=i, symbol=s, source=source[:16], created_at=now))
    session.commit()
    return picks


def state(session: Session, now: datetime | None = None) -> dict:
    """What the cockpit box shows: today's list (fixed or not) and the list it edits."""
    now = now or eastern_now()
    today = session_day(now)
    nxt = edit_day(now)
    out: dict = {"now": now.isoformat(timespec="seconds"), "max": MAX_PICKS,
                 "edit_day": nxt.isoformat(), "edit": get_picks(session, nxt)}
    if today is not None:
        out["today"] = today.isoformat()
        out["today_picks"] = get_picks(session, today)
        out["today_locked"] = locked(today, now)
    else:
        out["today"], out["today_picks"], out["today_locked"] = None, [], True
    # the live list = the session day's picks (the edit list IS today's before 09:30)
    out["live"] = out["today_picks"]
    return out


def live_picks(session: Session, now: datetime | None = None) -> list[str]:
    """The names the collector pulls now: today's list on a trading day, else none."""
    now = now or eastern_now()
    d = session_day(now)
    return get_picks(session, d) if d else []
