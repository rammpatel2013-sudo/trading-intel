"""Scheduled job (daily): build + deliver the Jaguar daily brief.

Reads the three core Jaguar emails, grounds + condenses them, cross-checks each named
print against our tape, computes S&P breadth, and pushes the signal-first brief to
Telegram (a compact summary + the HTML document). Read-only over the banked tables;
his calls are relayed descriptively and the ⚡ structures are illustrative analysis,
never an automated signal (FlashAlpha rule 4).

Manual run:
    python -m trading_intel.scheduler.jobs.jaguar_brief
"""

from __future__ import annotations

import html as _html
import uuid
from datetime import date
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy.orm import Session

from trading_intel.clients.telegram import TelegramClient
from trading_intel.config import Settings, get_settings
from trading_intel.jaguar.brief import build_jaguar_brief

log = structlog.get_logger(__name__)
_OUT = Path("reports")


def _summary(brief: dict[str, Any]) -> str:
    """Compact Telegram summary — the top names + their defined-risk structure."""
    lines = [f"<b>🐆 Jaguar Daily {brief.get('as_of', '')}</b>"]
    for t in (brief.get("trades") or [])[:6]:
        st = t.get("structure") or {}
        tag = f" ({_html.escape(t['tag'])})" if t.get("tag") else ""
        line = f"• <b>{_html.escape(t['ticker'])}</b>{tag}"
        if st.get("label"):
            line += f" — {_html.escape(st['label'])}"
        lines.append(line)
    if len(lines) == 1:
        lines.append("No trade callouts in today's letters.")
    lines.append("<i>Full brief attached ⬇️ — descriptive, not advice (rule 4).</i>")
    return "\n".join(lines)


def run(
    session: Session,
    *,
    settings: Settings | None = None,
    client: TelegramClient | None = None,
    cvforge: object | None = None,
) -> dict:
    """Build the Jaguar brief, write it, and push via Telegram."""
    settings = settings or get_settings()
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="jaguar_brief")

    cv = cvforge
    if cv is None:
        try:
            from trading_intel.clients.cvforge import CVForgeClient

            cv = CVForgeClient(settings)
        except Exception:
            cv = None

    html_doc, brief = build_jaguar_brief(session, settings, cvforge=cv)

    _OUT.mkdir(parents=True, exist_ok=True)
    dest = _OUT / f"jaguar_daily_{date.today().isoformat()}.html"
    dest.write_text(html_doc, encoding="utf-8")

    tele = client or TelegramClient(settings)
    delivered = tele.send_message(_summary(brief)) and tele.send_document(
        dest, caption="Jaguar daily brief"
    )
    bound.info(
        "jaguar_brief.done",
        file=str(dest),
        delivered=delivered,
        trades=len(brief.get("trades") or []),
    )
    return {"file": str(dest), "delivered": delivered, "trades": len(brief.get("trades") or [])}


def main() -> None:
    from trading_intel.memory.db import make_session_factory

    settings = get_settings()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    session_factory = make_session_factory(settings)
    with session_factory() as session:
        result = run(session, settings=settings)
    print(f"Done: {result}")


if __name__ == "__main__":
    main()
