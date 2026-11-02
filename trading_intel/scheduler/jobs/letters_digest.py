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
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_intel.clients.telegram import TelegramClient
from trading_intel.config import Settings, get_settings
from trading_intel.memory.models import GreeksSnapshot, ResearchNote
from trading_intel.synthesis.daily_brief import build_brief_html
from trading_intel.timeutils import is_trading_session

log = structlog.get_logger(__name__)
_OUT = Path("reports")


def _telegram_summary(ctx: dict[str, Any]) -> str:
    """Compact HTML text summary pushed alongside the HTML document."""
    lines = [f"<b>📈 Trading-Intel Daily {ctx.get('as_of')}</b>"]
    mr = ctx.get("market_read") or {}
    if mr.get("narrative"):
        lines.append(f"🧭 <b>Read:</b> {_html.escape(str(mr['narrative']))}")
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


def _upstream_fresh(session: Session, today: date) -> bool:
    """Check that the greeks snapshot collector ran for today.

    The daily brief is only meaningful when upstream jobs (greeks_snapshot,
    vix_snapshot, breadth) have banked today's data. The greeks snapshot is
    the bellwether: if it's missing, the brief would rehash yesterday's board.
    """
    latest_ts = session.execute(
        select(func.max(GreeksSnapshot.ts))
    ).scalar()
    if latest_ts is None:
        return False
    return latest_ts.date() >= today


def _letters_fresh(session: Session, today: date) -> bool:
    """True when ``letters_fetch`` has already stored at least one source note today.

    The digest renders newsletter commentary from ResearchNote rows keyed on the
    letter's date. If no note has ``as_of >= today - 1`` the fetch likely hasn't
    run yet (backlog #13 — chain digest off fetch).
    """
    latest = session.execute(
        select(func.max(ResearchNote.as_of))
        .where(ResearchNote.model == "letters")
    ).scalar()
    if latest is None:
        return False
    # Letters are dated by their own publication date, not the fetch date, so a
    # letter from yesterday is normal. We only flag "stale" when the newest
    # stored body is more than 2 days old — that means the fetch hasn't run.
    return (today - latest).days <= 2


def _ensure_letters_fetched(session: Session, settings: Settings, today: date) -> None:
    """Run ``letters_fetch`` inline if its notes are stale (#13).

    The NAS runs both jobs as independent DSM tasks. If the digest fires before
    the fetch (or the fetch task is disabled), the brief's commentary section
    would show stale letter bodies. Running the fetch here makes the digest
    self-sufficient without requiring DSM task ordering.
    """
    if _letters_fresh(session, today):
        return
    log.info("letters_digest.running_upstream_fetch", reason="notes_stale")
    try:
        from trading_intel.scheduler.jobs import letters_fetch
        from trading_intel.synthesis.llm import OllamaProvider

        llm = OllamaProvider(settings)
        letters_fetch.run(session, llm, settings=settings)
    except Exception as exc:  # noqa: BLE001 — best-effort; brief still renders
        log.warning("letters_digest.upstream_fetch_failed", error=str(exc))


def run(
    session: Session,
    *,
    settings: Settings | None = None,
    client: TelegramClient | None = None,
) -> dict:
    """Build the daily brief, write it, and push via Telegram."""
    settings = settings or get_settings()
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="letters_digest")
    today = date.today()
    if not is_trading_session(today):
        bound.info("letters_digest.skipped_non_session", as_of=str(today))
        return {"skipped": "not_a_trading_session", "as_of": str(today)}
    # Chain off letters_fetch (#13): ensure letter notes are fresh before
    # building the brief. Runs the fetch inline if needed, best-effort.
    _ensure_letters_fetched(session, settings, today)
    if not _upstream_fresh(session, today):
        bound.warning(
            "letters_digest.upstream_stale",
            as_of=str(today),
            hint="greeks_snapshot has not banked today — brief would use stale data",
        )
        # Continue anyway: the brief degrades gracefully, and the warning is
        # logged so the NAS health page surfaces it. A hard skip here would
        # silently swallow the digest on late-running upstream jobs.
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
