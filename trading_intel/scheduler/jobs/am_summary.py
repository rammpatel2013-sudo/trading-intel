"""Scheduled job: generate the daily AM regime report.

Builds the research-watchlist-aware morning note from stored data (see
``synthesis/am_summary.py``), renders it to markdown via the local LLM (with a
deterministic tables-only fallback if Ollama is down), and upserts one row per
day into ``am_summaries``. Idempotent: ``ON CONFLICT (date) DO UPDATE`` so a
re-run on the same day refreshes the report rather than skipping (CLAUDE.md
rule 5).

Reads stored data only and emits no signals/alerts — descriptive regime
read-through (FlashAlpha rule 4). Daily LLM is the local Ollama model
(``LLM_DAILY_MODEL``), not the Anthropic API (rule 7, cost-aware).

Discord delivery is gated behind ``AM_REPORT_SEND_DISCORD`` and is currently a
no-op: no Discord client exists yet (``clients/discord.py`` is unbuilt).

Manual run:
    python -m trading_intel.scheduler.jobs.am_summary
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_intel.config import Settings, get_settings
from trading_intel.memory.models import AmSummary
from trading_intel.synthesis.am_summary import build_am_context, render_am_markdown
from trading_intel.synthesis.llm import LLMProvider

log = structlog.get_logger(__name__)


def _maybe_send_discord(markdown: str, *, settings: Settings, bound: structlog.BoundLogger) -> None:
    """Optional Discord delivery — no-op until a Discord client is built."""
    if not settings.AM_REPORT_SEND_DISCORD:
        return
    # TODO: wire clients/discord.py once it exists; chunk markdown to the 2000-char
    # Discord limit and POST to settings.DISCORD_WEBHOOK_URL.
    bound.warning("am_summary.discord_skipped", reason="discord_client_not_built")


def run(
    session: Session,
    llm: LLMProvider,
    *,
    settings: Settings | None = None,
) -> None:
    """Build today's AM report and upsert it into ``am_summaries``."""
    settings = settings or get_settings()
    correlation_id = uuid.uuid4().hex
    bound = log.bind(correlation_id=correlation_id, job="am_summary")

    ctx = build_am_context(session, settings)
    markdown, metadata = render_am_markdown(ctx, llm, settings, session=session)
    bound.info(
        "am_summary.built",
        as_of=ctx.as_of.isoformat(),
        symbols=len(ctx.watchlist),
        research=len(ctx.research),
        used_llm=metadata.get("used_llm"),
    )

    # Insert against the Table (not the ORM entity): the column is literally
    # named "metadata", which shadows SQLAlchemy's MetaData on the mapped class.
    values = {
        "date": ctx.as_of,
        "markdown": markdown,
        "metadata": metadata,
        "claude_model": metadata.get("model"),
        "tokens_used": None,
    }
    stmt = pg_insert(AmSummary.__table__).values(**values)
    update_cols = {k: stmt.excluded[k] for k in values if k != "date"}
    stmt = stmt.on_conflict_do_update(index_elements=["date"], set_=update_cols)
    session.execute(stmt)
    session.commit()

    _maybe_send_discord(markdown, settings=settings, bound=bound)
    bound.info("am_summary.done", date=ctx.as_of.isoformat())


def main() -> None:
    """Manual entrypoint: wire Settings -> session -> Ollama LLM, run once."""
    from trading_intel.memory.db import make_session_factory
    from trading_intel.synthesis.llm import OllamaProvider

    settings = get_settings()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    llm = OllamaProvider(settings)
    session_factory = make_session_factory(settings)
    with session_factory() as session:
        run(session, llm, settings=settings)


if __name__ == "__main__":
    main()
