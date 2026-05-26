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
