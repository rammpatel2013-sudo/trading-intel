"""One AI job at a time on the laptop's Ollama (2026-11-13).

Ollama runs on the laptop (the NAS has 3.8 GB RAM). Every NAS job that calls it
(letters_tag at night, the Jaguar brief, the evening report comments, the morning
Doc read) used to call it whenever it ran; two jobs at once made the laptop slow and
calls time out (11/12). Now:

* **Turns.** Each model call takes a Postgres advisory lock in the shared NAS
  database first, so only one job talks to Ollama at a time. A job that cannot get
  its turn within ``wait_s`` raises :class:`OllamaBusy`.
* **Doc window.** Weekdays 08:00-09:30 ET the laptop AI is kept for the Doc read
  (``purpose="doc_read"``); every other caller gets :class:`OllamaBusy` at once.
  The laptop guard (``scripts/laptop/ollama_guard.ps1``) closes Ollama at 09:30.

:class:`OllamaBusy` is a ``ConnectionError`` - the same exception the ollama client
raises when Ollama is down - so callers that already cope with "Ollama off" cope
with "Ollama busy" the same way. Embeddings do not take turns (tiny calls).

No DB (tests, a laptop off the LAN) -> no lock; the call goes ahead as before.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from datetime import time as dtime

import structlog

log = structlog.get_logger(__name__)

#: Advisory-lock key shared by every Ollama caller (any constant int8 works).
LOCK_KEY = 7_303_111_326
#: Weekday window kept for the morning Doc read (Eastern).
DOC_WINDOW = (dtime(8, 0), dtime(9, 30))
DOC_READ = "doc_read"

_ENGINES: dict[str, object] = {}


class OllamaBusy(ConnectionError):
    """The laptop AI is busy with another job, or reserved for the Doc read."""


def doc_window(now: datetime) -> bool:
    """True on weekdays 08:00-09:30 ET (``now`` is naive Eastern)."""
    return now.weekday() < 5 and DOC_WINDOW[0] <= now.time() < DOC_WINDOW[1]


def _now() -> datetime:
    from trading_intel.timeutils import eastern_now

    return eastern_now()


def _engine(url: str):
    eng = _ENGINES.get(url)
    if eng is None:
        from sqlalchemy import create_engine
        from sqlalchemy.pool import NullPool

        eng = create_engine(url, poolclass=NullPool, connect_args={"connect_timeout": 3})
        _ENGINES[url] = eng
    return eng


def _connect(settings: object):
    """An autocommit DB connection for the lock, or None (no Postgres / not reachable)."""
    url = getattr(settings, "DATABASE_URL", None)
    if not isinstance(url, str) or not url.startswith("postgresql"):
        return None
    try:
        return _engine(url).connect().execution_options(isolation_level="AUTOCOMMIT")
    except Exception as exc:  # noqa: BLE001 - no lock is better than no AI
        log.warning("ollama_lock.no_db", error=type(exc).__name__)
        return None


@contextmanager
def ollama_turn(
    settings: object,
    *,
    purpose: str = "general",
    wait_s: float = 600.0,
    poll_s: float = 5.0,
    now: datetime | None = None,
) -> Iterator[bool]:
    """Hold this job's turn on Ollama for the ``with`` block.

    Yields True with the lock held, False when there is no DB to lock on.
    Raises :class:`OllamaBusy` inside the Doc window (unless ``purpose`` is the Doc
    read) or when the turn does not come within ``wait_s`` seconds.
    """
    if purpose != DOC_READ and doc_window(now or _now()):
        raise OllamaBusy("weekdays 08:00-09:30 ET the laptop AI is kept for the Doc read")
    conn = _connect(settings)
    if conn is None:
        yield False
        return
    from sqlalchemy import text

    got = False
    try:
        deadline = time.monotonic() + max(0.0, wait_s)
        while True:
            got = bool(conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}).scalar())
            if got or time.monotonic() >= deadline:
                break
            time.sleep(poll_s)
        if not got:
            raise OllamaBusy(f"another AI job had the laptop Ollama for {wait_s:.0f}s")
        yield True
    finally:
        if got:
            try:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
            except Exception:  # noqa: BLE001 - closing the connection releases it anyway
                pass
        conn.close()
