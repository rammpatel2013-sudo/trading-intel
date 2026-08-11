"""DSM job wrapper: build the SPX Vol-Regime & Skew Monitor and push to Telegram.

DSM task = ``bash scripts/nas/run_job.sh vol_regime_report`` (&#8594; ``python -m
trading_intel.scheduler.jobs.vol_regime_report``). Layout lives in
``scripts/vol_regime_report.py`` (loaded via ``reports.build_vol_regime``); this
only adds the Telegram push. Descriptor only (rule 4).

Manual run:
    python -m trading_intel.scheduler.jobs.vol_regime_report
"""

from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)


def run(session=None, settings=None, *, push: bool = True) -> str:
    """Build the vol-regime monitor and push to Telegram. Returns path."""
    from trading_intel.config import get_settings
    from trading_intel.reports import build_vol_regime

    settings = settings or get_settings()
    path = build_vol_regime(settings=settings, session=session)
    if push:
        from trading_intel.clients.telegram import TelegramClient

        sent = TelegramClient(settings).send_document(
            path, caption="SPX Vol-Regime & Skew Monitor — skew / vol / dispersion trends"
        )
        log.info("vol_regime_report.pushed", path=path, telegram_sent=sent)
    return path


def main() -> None:
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    print(f"vol_regime report written: {run()}")


if __name__ == "__main__":
    main()
