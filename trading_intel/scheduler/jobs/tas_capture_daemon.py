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

2026-11-19 (tape stopped 11/13 11:03 after an internet drop and never came back): the daemon now
- logs in again (a new ``ConvexClient``) after 3 failed polls in a row or on a 401/403,
- backs off at most 60 s (was 10 min),
- stops yielding to a stale cockpit raw file after 10 minutes (the collector is dead, not slow),
- sends ONE Telegram alarm when no print has been stored for 10 minutes during market hours
  (and one "back" message when prints land again).
``scripts/nas/run_tas_daemon.sh`` starts it under a fixed container name every 15 minutes and does
nothing when it is already running, so a killed container is back within 15 minutes.

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
_MAX_BACKOFF_S = 60            # 2026-11-19: was 600 - a 10-minute nap cost 30 min at the 11/12 open
_RELOGIN_AFTER = 3             # failed polls in a row before a fresh login
_YIELD_MAX_S = 600             # stop yielding to a cockpit file stale this long
_ALARM_AFTER_S = 600           # no stored print this long during RTH -> one Telegram alarm


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


def _auth_error(text: str | None) -> bool:
    t = str(text or "")
    return "401" in t or "403" in t or "nauthor" in t or "orbidden" in t


def _alarm(settings, text: str) -> None:
    """One Telegram line; never lets a Telegram problem stop the tape."""
    try:
        from trading_intel.clients.telegram import TelegramClient

        TelegramClient(settings).send_message(text)
    except Exception as exc:  # noqa: BLE001
        log.warning("tas_daemon.alarm_failed", error=str(exc))


class _Watch:
    """State the main loop keeps between polls (pure: no I/O, unit-tested)."""

    def __init__(self, start) -> None:
        self.fails = 0
        self.stale_since = None
        self.last_ok = start
        self.alarmed = False
        self.gave_up = False

    def backoff(self, interval: int) -> float:
        return min(_MAX_BACKOFF_S, interval * 2 ** max(self.fails - 1, 0))

    def need_relogin(self, error: str | None) -> bool:
        return self.fails >= _RELOGIN_AFTER or _auth_error(error)

    def yield_now(self, now, stale: bool) -> bool:
        if not stale:
            self.stale_since = None
            self.gave_up = False
            return False
        if self.stale_since is None:
            self.stale_since = now
        return (now - self.stale_since).total_seconds() < _YIELD_MAX_S

    def check_alarm(self, now) -> str | None:
        """'down' once when nothing stored for _ALARM_AFTER_S in RTH, 'up' once when it recovers."""
        rth = (9, 40) <= (now.hour, now.minute) < (16, 0)
        quiet = (now - self.last_ok).total_seconds() >= _ALARM_AFTER_S
        if rth and quiet and not self.alarmed:
            self.alarmed = True
            return "down"
        if self.alarmed and not quiet:
            self.alarmed = False
            return "up"
        return None


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
    source = ConvexClient(settings)
    factory = make_session_factory(settings)
    log.info("tas_daemon.start", interval=interval)

    w = _Watch(max(eastern_now(), eastern_now().replace(hour=9, minute=30, second=0, microsecond=0)))
    polls, yields, relogins = 0, 0, 0
    while True:
        now = eastern_now()
        if now.weekday() >= 5 or now.hour >= _CLOSE_HOUR:
            break
        sig = w.check_alarm(now)
        if sig == "down":
            mins = int((now - w.last_ok).total_seconds() // 60)
            log.error("tas_daemon.alarm_no_prints", minutes=mins)
            _alarm(settings, f"⚠ <b>Tape capture</b>: no option prints stored for {mins} min ({now:%H:%M} ET). "
                             "The daemon keeps retrying and logs in again; check ~/ti_tas_capture_daemon.log.")
        elif sig == "up":
            _alarm(settings, f"✅ <b>Tape capture</b> is storing prints again ({now:%H:%M} ET).")
        if w.yield_now(now, _cockpit_stale(now)):
            yields += 1
            log.warning("tas_daemon.yield_to_cockpit", yields=yields)
            time.sleep(60)
            continue
        if w.stale_since is not None and not w.gave_up:
            w.gave_up = True
            log.warning("tas_daemon.yield_gave_up", stale_min=round((now - w.stale_since).total_seconds() / 60, 1))
        t0 = time.time()
        status, err = None, None
        with factory() as session:
            try:
                # force=False -> run() self-skips before 09:30; captures during RTH.
                status = tas_capture_job.run(session, source, settings=settings)
            except Exception as exc:  # noqa: BLE001 - keep the session alive on a blip
                log.warning("tas_daemon.poll_error", error=str(exc))
                status, err = "fetch_failed", str(exc)
        if status == "fetch_failed" and err is None:
            err = tas_capture_job._LAST_ERROR[0]
        polls += 1
        if status == "done":
            w.last_ok = eastern_now()
        if status == "fetch_failed":
            w.fails += 1
            if w.need_relogin(err):
                try:
                    source = ConvexClient(settings)   # fresh login (session cookie expired / network dropped)
                    relogins += 1
                    log.warning("tas_daemon.relogin", fails=w.fails, relogins=relogins)
                except Exception as exc:  # noqa: BLE001 - still offline: keep backing off
                    log.warning("tas_daemon.relogin_failed", error=str(exc))
            wait = w.backoff(interval)
            log.warning("tas_daemon.backoff", fails=w.fails, wait_s=wait)
            time.sleep(wait)
            continue
        w.fails = 0
        time.sleep(max(1.0, interval - (time.time() - t0)))   # 2026-11-05: fixed cadence; a 40k pull takes ~11 s

    log.info("tas_daemon.done", polls=polls, yields=yields, relogins=relogins)


if __name__ == "__main__":
    main()
