"""DSM job wrapper: build the EOD volatility report and push it to Telegram.

Same gap as ``flow_report`` -- the generator existed as a CLI + MCP tool with no
``scheduler.jobs`` entry point, so it could not be scheduled. DSM task is
``bash scripts/nas/run_job.sh eod_vol_report``.

Layout lives in ``scripts/eod_vol_report.py`` (via ``reports.build_eod_vol``);
this adds the LLM wiring and the Telegram push. Reads the VIX complex, skew and
term-structure history. Descriptor only (rule 4).

Schedule after the vol collectors settle -- ``vix_options`` 16:42,
``index_skew``, ``iv_tenor_snapshots`` 17:05. ~17:30 ET is safe.

Manual run:
    python -m trading_intel.scheduler.jobs.eod_vol_report
"""
from __future__ import annotations

from pathlib import Path

import structlog

log = structlog.get_logger(__name__)


def run(session=None, settings=None, *, push: bool = True, days: int = 252,
        llm: object | None = None) -> str:
    """Build the EOD vol report and push to Telegram. Returns the written path."""
    from trading_intel.config import get_settings
    from trading_intel.reports import build_eod_vol

    settings = settings or get_settings()

    if llm is None:
        try:
            from trading_intel.synthesis.llm import OllamaProvider

            llm = OllamaProvider(settings)
        except Exception as exc:  # pragma: no cover - optional leg
            log.warning("eod_vol_report.llm_unavailable", err=str(exc))
            llm = None

    path = build_eod_vol(days=days, llm=llm, settings=settings)
    if push:
        from trading_intel.clients.telegram import TelegramClient

        sent = TelegramClient(settings).send_document(
            path, caption="EOD volatility — VIX complex, skew, term structure"
        )
        log.info("eod_vol_report.pushed", path=str(path), telegram_sent=sent)
    return str(Path(path))


def main() -> None:
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    print(f"eod vol report written: {run()}")


if __name__ == "__main__":
    main()
