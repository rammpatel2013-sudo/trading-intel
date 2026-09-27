"""Time helpers — consistent Eastern-Time stamping across collectors.

Collectors must stamp rows in US/Eastern (the market's trading timezone)
*regardless* of the host machine's clock. Previously they used ``datetime.now()``
(naive local time), so a collector running on a UTC box stamped UTC — which made
intraday charts start at the wrong hour and broke the market-hours guard. Using
``eastern_now()`` everywhere fixes both: stored timestamps are always wall-clock
Eastern, and the dashboard (which treats stored naive times as Eastern) renders
them correctly with no per-chart conversion.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")


def eastern_now() -> datetime:
    """Current wall-clock time in US/Eastern, as a naive ``datetime``.

    Naive (no tzinfo) so it drops straight into the existing naive ``DateTime``
    columns and the ``is_market_hours`` / floor-to-slot logic, but its value is
    always Eastern no matter what timezone the host runs in.
    """
    return datetime.now(EASTERN).replace(tzinfo=None)


_US_MARKET_HOLIDAYS_2026 = frozenset({
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
})
# NYSE full-day closures. Extend each year (the Norseman clock/week-close logic
# and every per-session collector gate read this). 2025 kept for replays.
_US_MARKET_HOLIDAYS = frozenset({
    "2025-01-01", "2025-01-09", "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26",
    "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27", "2025-12-25",
    *_US_MARKET_HOLIDAYS_2026,
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
    "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
})


def is_trading_session(d=None) -> bool:
    """True when ``d`` (default: today, Eastern) is a US equity trading session.

    Collectors that write one row per session MUST gate on this. Without it a
    weekend run re-reads the vendor's stale Friday values and banks them as a
    new session, which for a CUMULATIVE series is not a duplicate row but a
    permanent corruption: the 2026 breadth A-D line was walked down ~190 points
    every Saturday and Sunday before this gate existed.
    """
    d = d or eastern_now().date()
    if d.weekday() >= 5:
        return False
    return d.isoformat() not in _US_MARKET_HOLIDAYS
