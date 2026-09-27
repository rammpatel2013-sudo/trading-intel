"""DSM job wrapper: build the SPX skew & vanna report and push it to Telegram.

DSM task = ``bash scripts/nas/run_job.sh skew_vanna_report``. Layout lives in
``scripts/skew_vanna_report.py`` (via ``reports.build_skew_vanna``). Descriptor only.

Manual run:
    python -m trading_intel.scheduler.jobs.skew_vanna_report
"""

from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)


def run(session=None, settings=None, *, symbol: str = "SPX", push: bool = True) -> str:  # noqa: ANN001
    from trading_intel.config import get_settings
    from trading_intel.reports import build_skew_vanna

    settings = settings or get_settings()
    path = build_skew_vanna(symbol, settings=settings, session=session)
    if push:
        from trading_intel.clients.telegram import TelegramClient

        sent = TelegramClient(settings).send_document(path, caption=f"{symbol.upper()} Skew & Vanna")
        log.info("skew_vanna_report.pushed", path=path, telegram_sent=sent)
    return path


if __name__ == "__main__":  # pragma: no cover
    print(f"skew_vanna report written: {run()}")
