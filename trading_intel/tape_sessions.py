"""Option-tape session windows (2026-11-19, overnight collection plan, wave A1b).

Times are naive Eastern (the repo convention). A print's session comes from its own time:

    gth   20:15 -> 07:30   Cboe global trading hours (SPX / SPXW / XSP / VIX / RUT overnight)
    pre   07:30 -> 09:30   Cboe C1 pre-market (29 names since 2026-10-19) + SPX GTH tail
    rth   09:30 -> 16:00   regular hours
    post  16:00 -> 16:15   C1 post-market + SPX/SPXW until the index close
    curb  16:15 -> 17:00   Cboe curb (index options)

A GTH print after 20:15 belongs to the NEXT trading session (Cboe books it there), so its
``trade_date`` is the next trading day, not the calendar day. Prints between 17:00 and 20:15 have
no session (nothing trades) and are not captured.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from trading_intel.timeutils import is_trading_session

GTH_START = time(20, 15)
PRE_START = time(7, 30)
RTH_START = time(9, 30)
POST_START = time(16, 0)
CURB_START = time(16, 15)
CURB_END = time(17, 0)

GTH_POLL_S = 180  # overnight volume is thin: one tape pull every 3 minutes is enough


def next_trading_day(d: date) -> date:
    n = d + timedelta(days=1)
    for _ in range(10):
        if is_trading_session(n):
            return n
        n += timedelta(days=1)
    return n


def session_trade_date(ts: datetime) -> date:
    """The trading session a print at ``ts`` belongs to (GTH after 20:15 -> next session)."""
    if ts.time() >= GTH_START:
        return next_trading_day(ts.date())
    d = ts.date()
    return d if is_trading_session(d) else next_trading_day(d)


def tape_session(ts: datetime) -> str | None:
    """Session label for a print / poll time, or None when nothing trades (17:00-20:15, weekends).

    Respects the calendar: Friday 20:15 -> Sunday 20:15 is closed, and a session that would belong
    to a holiday is closed too."""
    t = ts.time()
    if CURB_END <= t < GTH_START:
        return None
    td = session_trade_date(ts)
    if t >= GTH_START:
        # evening GTH only when the next trading day is the very next weekday session
        # (Fri / pre-holiday evenings have no GTH bridging into a closed day)
        if (td - ts.date()).days > 1 and ts.weekday() != 6:  # Sunday evening -> Monday is fine
            return None
        return "gth"
    if not is_trading_session(ts.date()):
        return None
    if t < PRE_START:
        return "gth"
    if t < RTH_START:
        return "pre"
    if t < POST_START:
        return "rth"
    if t < CURB_START:
        return "post"
    return "curb"


def poll_interval(ts: datetime, base_s: int) -> int:
    """Seconds between tape pulls: thin overnight session polls slower."""
    return max(int(base_s), GTH_POLL_S) if tape_session(ts) == "gth" else int(base_s)
