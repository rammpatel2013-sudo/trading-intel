"""VIX expiration calendar — pure, dependency-free date math.

The standard (monthly) VIX future/option settles on the Wednesday that is 30
days *before* the third Friday of the following calendar month — i.e. 30 days
before the SPX expiration whose options VIX is computed from. If that Wednesday
(or the SPX Friday 30 days after it) is a Cboe holiday, settlement rolls back to
the immediately preceding business day. See Cboe VIX contract specs.

No vendor and no DB: the schedule is fully deterministic, so the collector job
(``scheduler/jobs/vix_expirations.py``) just computes it and the dashboard reads
the persisted rows. Weekly VIX expirations are intentionally out of scope here —
this is the standard monthly cycle only. Descriptive calendar data, not a signal
(FlashAlpha rule 4).
"""

from __future__ import annotations

from datetime import date, timedelta

_VIX_SETTLEMENT_OFFSET_DAYS = 30


def third_friday(year: int, month: int) -> date:
    """Third Friday of ``year``/``month`` (the standard SPX expiration)."""
    first = date(year, month, 1)
    # weekday(): Mon=0 … Sun=6; Friday=4.
    first_friday_day = 1 + (4 - first.weekday()) % 7
    return date(year, month, first_friday_day + 14)


def _easter(year: int) -> date:
    """Gregorian Easter Sunday (Anonymous/Meeus computus)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month = (h + m - 7 * n + 114) // 31
    day = ((h + m - 7 * n + 114) % 31) + 1
    return date(year, month, day)


def _observed(d: date) -> date:
    """US observed date for a fixed-date holiday (Sat→Fri, Sun→Mon)."""
    if d.weekday() == 5:  # Saturday
        return d - timedelta(days=1)
    if d.weekday() == 6:  # Sunday
        return d + timedelta(days=1)
    return d


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The ``n``-th ``weekday`` (Mon=0) of ``year``/``month`` (1-indexed)."""
    first = date(year, month, 1)
    first_match = 1 + (weekday - first.weekday()) % 7
    return date(year, month, first_match + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """The last ``weekday`` (Mon=0) of ``year``/``month``."""
    if month == 12:
        nxt = date(year + 1, 1, 1)
    else:
        nxt = date(year, month + 1, 1)
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def us_market_holidays(year: int) -> set[date]:
    """Cboe / NYSE full-day equity-market holidays for ``year``.

    Covers the closures relevant to settlement rolls: New Year's, MLK,
    Washington's Birthday, Good Friday, Memorial Day, Juneteenth (from 2022),
    Independence Day, Labor Day, Thanksgiving, and Christmas. Columbus Day and
    Veterans Day are federal but the equity market is open, so they are excluded.
    """
    days = {
        _observed(date(year, 1, 1)),  # New Year's Day
        _nth_weekday(year, 1, 0, 3),  # MLK Jr. Day — 3rd Monday Jan
        _nth_weekday(year, 2, 0, 3),  # Washington's Birthday — 3rd Monday Feb
        _easter(year) - timedelta(days=2),  # Good Friday
        _last_weekday(year, 5, 0),  # Memorial Day — last Monday May
        _observed(date(year, 7, 4)),  # Independence Day
        _nth_weekday(year, 9, 0, 1),  # Labor Day — 1st Monday Sep
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving — 4th Thursday Nov
        _observed(date(year, 12, 25)),  # Christmas Day
    }
    if year >= 2022:
        days.add(_observed(date(year, 6, 19)))  # Juneteenth (NYSE from 2022)
    return days


def is_market_holiday(d: date) -> bool:
    """True if ``d`` is a weekend or a full-day equity-market holiday."""
    return d.weekday() >= 5 or d in us_market_holidays(d.year)


def _prev_business_day(d: date) -> date:
    d -= timedelta(days=1)
    while is_market_holiday(d):
        d -= timedelta(days=1)
    return d


def vix_expiration_for_settlement_month(year: int, month: int) -> date:
    """Standard VIX expiration that settles in ``year``/``month``.

    Computed as the third Friday of the *following* month minus 30 days, with
    the Cboe holiday roll-back (if the Wednesday or its paired SPX Friday is a
    holiday, settle on the preceding business day).
    """
    if month == 12:
        spx_friday = third_friday(year + 1, 1)
    else:
        spx_friday = third_friday(year, month + 1)
    candidate = spx_friday - timedelta(days=_VIX_SETTLEMENT_OFFSET_DAYS)
    if is_market_holiday(candidate) or is_market_holiday(spx_friday):
        return _prev_business_day(candidate)
    return candidate


def next_vix_expirations(from_date: date, n: int = 8) -> list[date]:
    """The next ``n`` standard VIX expirations on or after ``from_date``.

    Walks forward by settlement month so the result is always chronological and
    de-duplicated (the holiday roll-back can never collide across months).
    """
    out: list[date] = []
    year, month = from_date.year, from_date.month
    # Step back one month so an expiration earlier this month that is still in
    # the future relative to ``from_date`` is not skipped.
    month -= 1
    if month == 0:
        year, month = year - 1, 12
    while len(out) < n:
        exp = vix_expiration_for_settlement_month(year, month)
        if exp >= from_date and (not out or exp > out[-1]):
            out.append(exp)
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out
