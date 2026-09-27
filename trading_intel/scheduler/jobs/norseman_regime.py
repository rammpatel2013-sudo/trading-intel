"""Scheduled job (EOD, after quotes_daily + breadth): Norseman regime monitor.

Thin wrapper around ``strategies.norseman_regime``: banks today's
``NORSEMAN_STATE``, detects NEW decision-point events (``NORSEMAN_EVENT``) and
pushes each to Telegram. Friday's close always sends the weekly grade.

Idempotent (rule 5): state is one row per session; every event carries a dedupe
key, so a re-run sends nothing new.

Manual run:
    python -m trading_intel.scheduler.jobs.norseman_regime            # emit + push
    python -m trading_intel.scheduler.jobs.norseman_regime --dry-run  # print only
"""

from __future__ import annotations

import sys
import uuid

import structlog
from sqlalchemy.orm import Session

from trading_intel.config import Settings, get_settings
from trading_intel.memory.db import make_session_factory
from trading_intel.strategies import norseman_regime as strategy

log = structlog.get_logger(__name__)

_ICON = {"critical": "🚨", "high": "⚠️", "medium": "🔔", "low": "ℹ️", "info": "🗓"}


def format_event(e: dict) -> str:
    if e["kind"] == "WEEKLY_GRADE":
        return e["text"]
    return f"{_ICON.get(e['severity'], '')} <b>NMT · {e['kind']}</b>\n{e['text']}"


def run(session: Session, *, settings: Settings | None = None, dry_run: bool = False) -> int:
    settings = settings or get_settings()
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="norseman_regime")
    if dry_run:
        st = strategy.read_state(session)
        events = strategy.detect_events(st) if st else []
        for e in events:
            print(format_event(e), "\n")
        return len(events)
    st, new = strategy.emit_signals(session)
    session.commit()
    if new:
        from trading_intel.clients.telegram import TelegramClient

        tg = TelegramClient(settings)
        for e in new:
            tg.send_message(format_event(e))
    bound.info("norseman_regime.done", as_of=str(st.as_of) if st else None,
               new_events=[e["kind"] for e in new])
    return len(new)


if __name__ == "__main__":  # pragma: no cover
    s = get_settings()
    with make_session_factory(s)() as sess:
        run(sess, settings=s, dry_run="--dry-run" in sys.argv)
