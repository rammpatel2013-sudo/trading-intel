"""Desk brief - one-time backfill (2026-11-14). ``run_job.sh desk_backfill`` (background, ~10-20 min).

1. FINRA Reg SHO daily files for the last ~260 sessions -> finra_short_volume (free, 1 GET per day;
   history for the dark-pool test and the 1W / 2W windows from day one).
2. Names (FMP company screener).
3. Convex und pull for the last session, if none is stored. On a weekend / after 18:00 ET the buy/sell
   split reads 0, so it is stored as missing (the brief then shows "options split missing").
4. desk_brief for the last session -> bars, gamma, page.
``--from-step N`` resumes. Idempotent.
"""

from __future__ import annotations

import argparse
import uuid
from datetime import date, timedelta

import structlog
from sqlalchemy.orm import Session

from trading_intel.config import Settings, get_settings
from trading_intel.desk import store
from trading_intel.timeutils import eastern_now, is_trading_session

log = structlog.get_logger(__name__)


def last_session(today: date, *, after_close: bool) -> date:
    d = today if after_close else today - timedelta(days=1)
    while not is_trading_session(d):
        d -= timedelta(days=1)
    return d


def sessions_back(end: date, n: int) -> list[date]:
    out, d = [], end
    while len(out) < n:
        if is_trading_session(d):
            out.append(d)
        d -= timedelta(days=1)
    return out


def finra_backfill(session: Session, end: date, n: int, *, finra: object | None = None) -> dict:
    if finra is None:
        from trading_intel.clients.finra import FinraClient

        finra = FinraClient()
    have = set(store.finra_dates(session, end, n + 30))
    done = missing = 0
    for d in sessions_back(end, n):
        if d in have:
            continue
        short = finra.short_volume_all(d)  # type: ignore[attr-defined]
        if not short:
            missing += 1
            continue
        store.save_finra(session, d, short)
        done += 1
        if done % 25 == 0:
            log.info("desk_backfill.finra.progress", done=done, last=str(d))
    return {"finra_days_added": done, "finra_days_missing": missing}


def run(
    session: Session,
    settings: Settings | None = None,
    *,
    from_step: int = 1,
    sessions: int = 260,
    app: object | None = None,
    finra: object | None = None,
    cv: object | None = None,
    src: object | None = None,
) -> dict:
    settings = settings or get_settings()
    now = eastern_now()
    day = last_session(now.date(), after_close=now.hour >= 17)
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="desk_backfill", day=str(day))
    out: dict = {"day": day.isoformat()}
    if from_step <= 1:
        out.update(finra_backfill(session, day, sessions, finra=finra))
        bound.info("desk_backfill.step1", **out)
    if cv is None:
        from trading_intel.clients.cvforge import CVForgeClient

        cv = CVForgeClient(settings)
    if src is None:
        from trading_intel.clients.equity_data import EquityDataSource

        src = EquityDataSource(cv)
    if from_step <= 2:
        from trading_intel.scheduler.jobs import desk_brief

        out["names"] = desk_brief.refresh_names(session, src, now.date(), force=True)
    if from_step <= 3 and not store.load_und(session, day):
        from trading_intel.scheduler.jobs import desk_snapshot

        if app is None:
            from trading_intel.clients.convex_app import ConvexAppClient

            app = ConvexAppClient(settings)
        rows = app.und_snapshot(min_option_volume=1)  # type: ignore[attr-defined]
        live = now.date() == day and 16 <= now.hour < 18
        ok = live and desk_snapshot.split_ok(rows)
        out["und_rows"] = store.save_und(session, day, rows, now.replace(tzinfo=None), split_ok=ok)
        out["und_split_ok"] = ok
    if from_step <= 4:
        from trading_intel.scheduler.jobs import desk_brief

        out["brief"] = desk_brief.run(session, settings, cv=cv, src=src, day=day, force=True)
    bound.info("desk_backfill.done", **{k: v for k, v in out.items() if k != "brief"})
    return out


def main() -> None:
    from trading_intel.memory.db import make_session_factory

    ap = argparse.ArgumentParser()
    ap.add_argument("--from-step", type=int, default=1)
    ap.add_argument("--sessions", type=int, default=260)
    a = ap.parse_args()
    s = get_settings()
    with make_session_factory(s)() as session:
        print(run(session, s, from_step=a.from_step, sessions=a.sessions))


if __name__ == "__main__":
    main()
