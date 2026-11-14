"""Desk brief - capture the Convex ``und`` screener once a day (2026-11-14).

ONE call: every optionable underlying's option volume and its bought / sold split (the options leg
of the desk brief). The split is session-cumulative and resets at ~18:00 ET, so this runs at 17:15 ET
(NAS DSM Mon-Fri, ``run_job.sh desk_snapshot``) - after the close, before the reset. Idempotent upsert
into ``desk_und_daily``. Descriptor only (rule 4).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

import structlog
from sqlalchemy.orm import Session

from trading_intel.config import Settings, get_settings
from trading_intel.desk import store
from trading_intel.timeutils import eastern_now, is_trading_session

log = structlog.get_logger(__name__)
SPLIT = ("volm_call_buy", "volm_call_sell", "volm_put_buy", "volm_put_sell")


def split_ok(rows: list[dict]) -> bool:
    """True when the buy/sell counters hold real numbers (all-zero = after the reset / weekend)."""
    return any((r.get(k) or 0) > 0 for r in rows for k in SPLIT)


def run(
    session: Session,
    settings: Settings | None = None,
    *,
    app: object | None = None,
    day: date | None = None,
    now: datetime | None = None,
    force: bool = False,
) -> dict:
    settings = settings or get_settings()
    now = now or eastern_now()
    day = day or now.date()
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="desk_snapshot")
    if not force and not is_trading_session(day):
        return {"skipped": "not_a_trading_session"}
    if now.hour >= 18 and not force:
        bound.warning(
            "desk_snapshot.after_reset", hint="buy/sell split resets ~18:00 ET; run at 17:15"
        )
    if app is None:
        from trading_intel.clients.convex_app import ConvexAppClient

        app = ConvexAppClient(settings)
    rows = app.und_snapshot(min_option_volume=1)  # type: ignore[attr-defined]
    ok = split_ok(rows)
    n = store.save_und(session, day, rows, now.replace(tzinfo=None), split_ok=ok)
    out = {"day": day.isoformat(), "rows": n, "split_ok": ok}
    (bound.info if ok else bound.warning)("desk_snapshot.done", **out)
    return out


def main() -> None:
    from trading_intel.memory.db import make_session_factory

    s = get_settings()
    with make_session_factory(s)() as session:
        run(session, s)


if __name__ == "__main__":
    main()
