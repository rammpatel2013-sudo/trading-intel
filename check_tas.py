"""Throwaway check: is the TAS options-tape capture actually collecting?

Reads DATABASE_URL from .env via Settings and reports, per trade_date, how many
prints landed in tas_prints and when the last one was captured. If the most
recent trade_date is today (a weekday) and captured_at is recent, the NAS task
is working. Delete this file when done.
"""

from sqlalchemy import text

from trading_intel.config import get_settings
from trading_intel.memory.db import make_session_factory

session = make_session_factory(get_settings())()

total = session.execute(text("select count(*) from tas_prints")).scalar()
print(f"tas_prints total rows: {total}\n")

if total:
    rows = session.execute(
        text(
            "select trade_date, count(*) as prints, "
            "min(ts) as first_ts, max(ts) as last_ts, "
            "max(captured_at) as last_captured "
            "from tas_prints group by trade_date order by trade_date desc limit 10"
        )
    ).all()
    print(f"{'trade_date':<12}{'prints':>8}  {'first_ts':<20}{'last_ts':<20}last_captured")
    for r in rows:
        print(
            f"{str(r.trade_date):<12}{r.prints:>8}  "
            f"{str(r.first_ts):<20}{str(r.last_ts):<20}{r.last_captured}"
        )
else:
    print("No rows yet — the capture task has never written, or isn't deployed.")
