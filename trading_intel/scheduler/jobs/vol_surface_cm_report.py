"""DSM job wrapper: build the CM vol-surface-changes board and push to Telegram.

DSM task = ``bash scripts/nas/run_job.sh vol_surface_cm_report`` (-> ``python -m
trading_intel.scheduler.jobs.vol_surface_cm_report``). Layout lives in
``scripts/vol_surface_cm_report.py`` (via ``reports.build_vol_surface_cm``); this
only adds the Telegram push. Descriptor only (rule 4).

Manual run:
    python -m trading_intel.scheduler.jobs.vol_surface_cm_report
"""

from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)


def run(session=None, settings=None, *, symbol: str = "SPX", push: bool = True) -> str:
    """Build the CM vol-surface board and push to Telegram. Returns path."""
    from trading_intel.config import get_settings
    from trading_intel.reports import build_vol_surface_cm

    settings = settings or get_settings()
    path = build_vol_surface_cm(symbol, settings=settings, session=session)
    if push:
        from trading_intel.clients.telegram import TelegramClient

        sent = TelegramClient(settings).send_document(
            path, caption=f"{symbol.upper()} Volatility Surface Changes (constant-maturity)"
        )
        log.info("vol_surface_cm_report.pushed", path=path, telegram_sent=sent)
    return path


def main() -> None:
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    print(f"vol_surface_cm report written: {run()}")


if __name__ == "__main__":
    main()
