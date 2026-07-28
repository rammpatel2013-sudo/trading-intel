"""Scheduled job: build + deliver the daily investor-letters brief.

Builds the daily brief — index gamma board (SPX/SPY/QQQ/VIX flip + trend), Doc
McGraw levels + expectation, vol state, letters commentary, trade tracker,
what-was-learned and cross-checks — via ``synthesis.daily_brief`` and delivers
it through Telegram (a text summary + the HTML as a document). Read-only over
the banked tables; descriptive research context only (FlashAlpha rule 4).

Manual run:
    python -m trading_intel.scheduler.jobs.letters_digest
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
from trading_intel.synthesis.daily_brief import build_brief_html

log = structlog.get_logger(__name__)
_OUT = Path("reports")


def _telegram_summary(ctx: dict[str, Any]) -> str:
    """Compact HTML text summary pushed alongside the HTML document."""
    lines = [f"<b>📈 Trading-Intel Daily {ctx.get('as_of')}</b>"]
    for ix in ctx.get("indices") or []:
        vf = ix.get("spot_vs_flip_pct")
        arrow = "▼" if (vf or 0) < 0 else "▲"
        spot = ix.get("spot")
        flip = ix.get("flip")
        if spot is not None and flip is not None:
            lines.append(
                f"{_html.escape(ix['symbol'])}: {spot:,.0f} {arrow} flip {flip:,.0f}"
                if spot > 100
                else f"{_html.escape(ix['symbol'])}: {spot:,.2f} {arrow} flip {flip:,.2f}"
            )
    vix = ctx.get("vix") or {}
    if vix.get("vix") is not None:
        lines.append(f"VIX {vix.get('vix')} · VVIX {vix.get('vvix')}")
    if ctx.get("learned_total") is not None:
        lines.append(f"{ctx['learned_total']} letter rows ingested today")
    lines.append("<i>Full brief attached ⬇️ — descriptive only (rule 4).</i>")
    return "\n".join(lines)


def run(
    session: Session,
    *,
    settings: Settings | None = None,
    client: TelegramClient | None = None,
) -> dict:
    """Build the daily brief, write it, and push via Telegram."""
    settings = settings or get_settings()
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="letters_digest")
    html_doc, ctx = build_brief_html(session, settings)

    _OUT.mkdir(parents=True, exist_ok=True)
    dest = _OUT / f"daily_brief_{date.today().isoformat()}.html"
    dest.write_text(html_doc, encoding="utf-8")

    tele = client or TelegramClient(settings)
    delivered = tele.send_message(_telegram_summary(ctx)) and tele.send_document(
        dest, caption="Trading-Intel daily brief"
    )

    bound.info(
        "letters_digest.done",
        file=str(dest),
        delivered=delivered,
        indices=len(ctx.get("indices") or []),
        learned=ctx.get("learned_total"),
    )
    return {"file": str(dest), "delivered": delivered, "learned": ctx.get("learned_total")}


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
