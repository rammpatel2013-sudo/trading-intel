"""DSM job wrapper: build the sector lead/lag report and push it to Telegram.

Lets the NAS dispatcher run the report like any collector — the DSM task is
``bash scripts/nas/run_job.sh sector_greeks sector_report`` (collect the SPDRs
from CVForge, then render + push). Report layout lives in
``scripts/sector_report.py`` (loaded via ``reports.build_sector``); this only
adds the Telegram push. Descriptor only (rule 4).

Manual run:
    python -m trading_intel.scheduler.jobs.sector_report
"""
from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)


def run(session=None, settings=None, *, push: bool = True) -> str:
    """Build the sector report (via reports.build_sector) and push to Telegram. Returns path."""
    from trading_intel.config import get_settings
    from trading_intel.reports import build_sector

    settings = settings or get_settings()
    path = build_sector(settings=settings)
    if push:
        from trading_intel.clients.telegram import TelegramClient

        sent = TelegramClient(settings).send_document(
            path, caption="Sector lead/lag + fragility"
        )
        log.info("sector_report.pushed", path=path, telegram_sent=sent)
    return path


def main() -> None:
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    print(f"sector report written: {run()}")


if __name__ == "__main__":
    main()
