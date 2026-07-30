"""DSM job wrapper: build the SPX/SPY cockpit and push it to Telegram.

Lets the NAS dispatcher run the report like any collector — the DSM task is just
``bash scripts/nas/run_job.sh cockpit_report`` (→ ``python -m
trading_intel.scheduler.jobs.cockpit_report``). Report layout lives in
``scripts/cockpit_report.py`` (loaded via ``reports.build_cockpit``); this only
adds the Telegram push so the scheduled run posts automatically. Descriptor only
(rule 4).

Manual run:
    python -m trading_intel.scheduler.jobs.cockpit_report
"""
from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)


def run(session=None, settings=None, *, push: bool = True) -> str:
    """Build the cockpit (via reports.build_cockpit) and push to Telegram. Returns path."""
    from trading_intel.config import get_settings
    from trading_intel.reports import build_cockpit

    settings = settings or get_settings()
    path = build_cockpit(settings=settings)
    if push:
        from trading_intel.clients.telegram import TelegramClient

        sent = TelegramClient(settings).send_document(
            path, caption="SPX / SPY dealer-positioning cockpit"
        )
        log.info("cockpit_report.pushed", path=path, telegram_sent=sent)
    return path


def main() -> None:
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    print(f"cockpit written: {run()}")


if __name__ == "__main__":
    main()
