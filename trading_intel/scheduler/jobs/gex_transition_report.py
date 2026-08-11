"""DSM job wrapper: build the SPX GEX-Transition Signal report and push to Telegram.

DSM task = ``bash scripts/nas/run_job.sh gex_transition_report`` (&#8594; ``python -m
trading_intel.scheduler.jobs.gex_transition_report``). Layout lives in
``scripts/gex_transition_report.py`` (loaded via ``reports.build_gex_transition``);
this only adds the Telegram push. Descriptor only (rule 4).

Manual run:
    python -m trading_intel.scheduler.jobs.gex_transition_report
"""

from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)


def run(session=None, settings=None, *, push: bool = True) -> str:
    """Build the GEX-transition report and push to Telegram. Returns path."""
    from trading_intel.config import get_settings
    from trading_intel.reports import build_gex_transition

    settings = settings or get_settings()
    path = build_gex_transition(settings=settings, session=session)
    if push:
        from trading_intel.clients.telegram import TelegramClient

        sent = TelegramClient(settings).send_document(
            path, caption="SPX GEX-Transition Signal — dealer-gamma quiet-unwind state"
        )
        log.info("gex_transition_report.pushed", path=path, telegram_sent=sent)
    return path


def main() -> None:
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    print(f"gex_transition report written: {run()}")


if __name__ == "__main__":
    main()
