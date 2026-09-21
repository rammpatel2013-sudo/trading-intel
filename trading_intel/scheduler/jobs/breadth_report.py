"""DSM job wrapper: build the market-breadth board and push it to Telegram.

DSM task = ``bash scripts/nas/run_job.sh breadth_report`` (-> ``python -m
trading_intel.scheduler.jobs.breadth_report``). Layout lives in
``scripts/breadth_report.py``; this only adds the Telegram push.

Schedule AFTER the ``breadth`` collector (~16:30 ET) so the board carries the
session just closed. Descriptor only (rule 4).
"""

from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)


def run(session=None, settings=None, *, push: bool = True, days: int = 120) -> str:
    """Build the breadth board and push to Telegram. Returns the written path."""
    import sys
    from pathlib import Path

    from trading_intel.config import get_settings

    settings = settings or get_settings()
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import breadth_report as impl  # noqa: PLC0415

    path = impl.build(days=days, settings=settings)
    if push:
        from trading_intel.clients.telegram import TelegramClient

        sent = TelegramClient(settings).send_document(
            path, caption="Market Breadth — A-D line, participation, new H/L, McClellan"
        )
        log.info("breadth_report.pushed", path=path, telegram_sent=sent)
    return path


def main() -> None:
    structlog.configure(processors=[structlog.processors.add_log_level,
                                    structlog.processors.TimeStamper(fmt="iso"),
                                    structlog.processors.JSONRenderer()])
    print(f"breadth board written: {run()}")


if __name__ == "__main__":
    main()
