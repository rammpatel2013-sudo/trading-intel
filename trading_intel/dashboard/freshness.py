"""Data-freshness helpers for dashboard pages.

Every page should tell the reader *when* the data it shows was last pulled, so a
stale view (e.g. an AM report from last Friday) is obvious at a glance. The
collectors stamp rows with naive local time on a NAS configured to
``America/New_York``, so a stored naive timestamp is already Eastern — these
helpers render it consistently with an explicit ``ET`` label and compute a simple
freshness state for colour-coding.

Pure (no DB, no Streamlit): pages pass in the latest timestamp they loaded.
Descriptive only — a freshness read, never a signal (FlashAlpha rule 4).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

ET_LABEL = "ET"


def format_et(ts: datetime | date | None) -> str:
    """Render a stored timestamp as an Eastern-Time string.

    ``datetime`` → ``"2026-05-26 16:35 ET"``; a date-only value →
    ``"2026-05-26"``; ``None`` → ``"no data yet"``. Naive timestamps are treated
    as already-Eastern (the NAS runs on ET); aware ones are converted.
    """
    if ts is None:
        return "no data yet"
    if isinstance(ts, datetime):
        local = ts
        if ts.tzinfo is not None:
            from zoneinfo import ZoneInfo

            local = ts.astimezone(ZoneInfo("America/New_York"))
        return f"{local:%Y-%m-%d %H:%M} {ET_LABEL}"
    if isinstance(ts, date):
        return f"{ts:%Y-%m-%d}"
    return str(ts)


def freshness_caption(ts: datetime | date | None, *, label: str = "Last data pulled") -> str:
    """One-line freshness caption, e.g. ``"Last data pulled: 2026-05-26 16:35 ET"``."""
    return f"{label}: {format_et(ts)}"


def age(ts: datetime | date | None, *, now: datetime | None = None) -> timedelta | None:
    """Age of ``ts`` relative to ``now`` (default ``datetime.now()``). ``None`` if no ts.

    A date-only value is anchored at midnight so daily snapshots still age.
    """
    if ts is None:
        return None
    now = now or datetime.now()
    moment = ts if isinstance(ts, datetime) else datetime(ts.year, ts.month, ts.day)
    if moment.tzinfo is not None:
        moment = moment.replace(tzinfo=None)
    return now - moment


def staleness(
    ts: datetime | date | None,
    *,
    fresh_within_hours: float,
    now: datetime | None = None,
) -> str:
    """Freshness state for colour-coding: ``fresh`` / ``stale`` / ``unknown``.

    ``fresh`` when the data is younger than ``fresh_within_hours`` (set per page:
    a few minutes for intraday, ~a day for EOD snapshots). ``unknown`` if no ts.
    """
    delta = age(ts, now=now)
    if delta is None:
        return "unknown"
    return "fresh" if delta <= timedelta(hours=fresh_within_hours) else "stale"
