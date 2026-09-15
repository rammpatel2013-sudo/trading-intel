"""DSM job wrapper: build the EOD option-tape Flow Report and push it to Telegram.

The report generator has existed since the TAS roll-up shipped, but only as a
CLI + MCP tool -- there was no ``scheduler.jobs`` entry point, so the NAS could
never schedule it. This adds one, so the DSM task is just
``bash scripts/nas/run_job.sh flow_report``.

Layout lives in ``scripts/flow_report.py`` (loaded via ``reports.build_flow``);
this only adds the LLM wiring and the Telegram push. Reads the DURABLE
``tas_daily_flow`` / ``tas_daily_contract`` roll-ups, so it is unaffected by the
60-day ``tas_prints`` prune. Descriptor only (rule 4).

Schedule AFTER the 17:05 ET ``tas_daily_rollup`` -- it reads what that job
writes. ~17:25 ET is safe.

Manual run:
    python -m trading_intel.scheduler.jobs.flow_report
"""
from __future__ import annotations

from pathlib import Path

import structlog

log = structlog.get_logger(__name__)


def run(
    session=None,
    settings=None,
    *,
    push: bool = True,
    lookback_days: int = 21,
    recent_days: int = 5,
    min_notional: float = 1_000_000.0,
    llm: object | None = None,
) -> str:
    """Build the flow report and push to Telegram. Returns the written path."""
    from trading_intel.config import get_settings
    from trading_intel.reports import build_flow

    settings = settings or get_settings()

    # Local Ollama only (rule 7). run_job.sh points OLLAMA_HOST at the laptop; if
    # it is asleep the narrative is simply omitted -- build_flow degrades silently.
    if llm is None:
        try:
            from trading_intel.synthesis.llm import OllamaProvider

            llm = OllamaProvider(settings)
        except Exception as exc:  # pragma: no cover - optional leg
            log.warning("flow_report.llm_unavailable", err=str(exc))
            llm = None

    path = build_flow(
        lookback_days=lookback_days,
        recent_days=recent_days,
        min_notional=min_notional,
        llm=llm,
        settings=settings,
    )
    if push:
        from trading_intel.clients.telegram import TelegramClient

        sent = TelegramClient(settings).send_document(
            path, caption="EOD option flow — accumulation / distribution"
        )
        log.info("flow_report.pushed", path=str(path), telegram_sent=sent)
    return str(Path(path))


def main() -> None:
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    print(f"flow report written: {run()}")


if __name__ == "__main__":
    main()
