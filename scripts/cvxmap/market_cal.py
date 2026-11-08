"""market_cal.py - created 2026-11-08 (candidate, not shipped). Exchange calendar for model expiry endpoints.

Source: Cboe U.S. Options hours page (cboe.com/about/hours/us-options/), read 2026-11-08:
  2026 holidays: Jan 1, Jan 19, Feb 16, Apr 3, May 25, Jun 19, Jul 3, Sep 7, Nov 26, Dec 25;
       early closes 13:00 ET: Nov 27, Dec 24.
  2027 holidays: Jan 1, Jan 18, Feb 15, Mar 26, May 31, Jun 18, Jul 5, Sep 6, Nov 25, Dec 24;
       early close 13:00 ET: Nov 26.
Dates after VERIFIED_THROUGH are flagged unverified (the cockpit shows it); extend the lists each year.

Model endpoints (ET wall clock, returned with the UTC offset of that date so DST spans are exact):
  PM-settled (SPXW, SPY, QQQ): the session close of the expiry date - 16:00, 13:00 on an early-close day.
  AM-settled SPX monthly: 09:30 of the expiry date (the special opening quotation is built from component
  opening prints after 09:30; 09:30 is the model endpoint, not the settlement value's timestamp). Its last
  trade is the prior session's close; the model keeps its exposure until 09:30 because it still settles then.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
VERIFIED_THROUGH = dt.date(2027, 12, 31)
HOLIDAYS = {dt.date(2026, 1, 1), dt.date(2026, 1, 19), dt.date(2026, 2, 16), dt.date(2026, 4, 3), dt.date(2026, 5, 25),
            dt.date(2026, 6, 19), dt.date(2026, 7, 3), dt.date(2026, 9, 7), dt.date(2026, 11, 26), dt.date(2026, 12, 25),
            dt.date(2027, 1, 1), dt.date(2027, 1, 18), dt.date(2027, 2, 15), dt.date(2027, 3, 26), dt.date(2027, 5, 31),
            dt.date(2027, 6, 18), dt.date(2027, 7, 5), dt.date(2027, 9, 6), dt.date(2027, 11, 25), dt.date(2027, 12, 24)}
EARLY_CLOSE = {dt.date(2026, 11, 27): (13, 0), dt.date(2026, 12, 24): (13, 0), dt.date(2027, 11, 26): (13, 0)}


def session_close(d: dt.date) -> tuple[int, int]:
    return EARLY_CLOSE.get(d, (16, 0))


def is_session(d: dt.date) -> bool:
    return d.weekday() < 5 and d not in HOLIDAYS


def verified(d: dt.date) -> bool:
    return d <= VERIFIED_THROUGH


def model_endpoint(expiry: dt.date, family: str) -> str:
    """ISO timestamp with offset, e.g. 2026-11-27T13:00:00-05:00 (PM, early close) / 2026-11-20T09:30:00-05:00 (AM)."""
    h, m = (9, 30) if family == "AM" else session_close(expiry)
    return dt.datetime(expiry.year, expiry.month, expiry.day, h, m, tzinfo=ET).isoformat()
