"""Long-lived TAS capture daemon for the NAS (one login, polls until the close).

The per-poll logic lives in ``tas_capture_job.run``; this wraps it in a single
long-running process so the NAS runs **one container per session** — one Convex
login, no per-minute cold starts or repeated logins. A DSM task starts it around
09:29 ET on weekdays; it polls every ``TAS_POLL_INTERVAL`` seconds (default 30)
and exits at 16:00 ET so the container stops cleanly.

Each poll reuses the idempotent ``tas_capture_job.run`` (self-guards market
hours + zeroed tape, upserts on the print key), so a restart mid-session just
resumes — no duplicates (rule 5). Rule 1: the only Convex entry point is
``ConvexClient``. Rule 4: descriptive capture, no signals.

Run (manual; exits at 16:00 ET):
    python -m trading_intel.scheduler.jobs.tas_capture_daemon
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import structlog

from trading_intel.clients.convex import ConvexClient
from trading_intel.config import get_settings
from trading_intel.memory.db import make_session_factory
from trading_intel.scheduler.jobs import tas_capture_job
from trading_intel.timeutils import eastern_now

log = structlog.get_logger(__name__)

_CLOSE_HOUR = 16
_DEFAULT_INTERVAL = 30
# 2026-11-05 safety: the cockpit collector comes first. Its raw file is appended every minute; if it goes
# stale (collector slow, throttled or stuck) the tape pull steps aside until the cockpit is writing again.
_COCKPIT_RAW = Path(os.environ.get("TI_DIR", "/app")) / "scripts" / "cvxmap" / "data"
_COCKPIT_STALE_S = 150
_MAX_BACKOFF_S = 600


def _cockpit_stale(now) -> bool:
    """True when today's cockpit raw file exists but has not been written for _COCKPIT_STALE_S seconds.
    No file at all = collector not running today -> never blocks the tape."""
    if (now.hour, now.minute) < (9, 35):
        return False
    fp = _COCKPIT_RAW / f"raw_{now:%Y%m%d}.ndjson.gz"
    try:
        return (time.time() - fp.stat().st_mtime) > _COCKPIT_STALE_S
    except OSError:
        return False


def main() -> None:
    """Poll the tape until the close, reusing the one-shot capture each cycle."""
    settings = get_settings()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    interval = int(getattr(settings, "TAS_POLL_INTERVAL", _DEFAULT_INTERVAL))
    source = ConvexClient(settings)  # single login for the whole session
    factory = make_session_factory(settings)
    log.info("tas_daemon.start", interval=interval)

    polls, fails, yields = 0, 0, 0
    while True:
        now = eastern_now()
        if now.weekday() >= 5 or now.hour >= _CLOSE_HOUR:
            break
        if _cockpit_stale(now):
            yields += 1
            log.warning("tas_daemon.yield_to_cockpit", yields=yields)
            time.sleep(60)
            continue
        t0 = time.time()
        status = None
        with factory() as session:
            try:
                # force=False -> run() self-skips before 09:30; captures during RTH.
                status = tas_capture_job.run(session, source, settings=settings)
            except Exception as exc:  # noqa: BLE001 - keep the session alive on a blip
                log.warning("tas_daemon.poll_error", error=str(exc))
                status = "fetch_failed"
        polls += 1
        if status == "fetch_failed":       # throttled / dropped: back off 30 s, 60 s, 120 s ... max 10 min
            fails += 1
            wait = min(_MAX_BACKOFF_S, interval * 2 ** (fails - 1))
            log.warning("tas_daemon.backoff", fails=fails, wait_s=wait)
            time.sleep(wait)
            continue
        fails = 0
        time.sleep(max(1.0, interval - (time.time() - t0)))   # 2026-11-05: fixed cadence; a 40k pull takes ~11 s

    log.info("tas_daemon.done", polls=polls, yields=yields)


if __name__ == "__main__":
    main()
