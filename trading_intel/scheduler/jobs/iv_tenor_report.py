"""DSM job wrapper: index-ETF constant-maturity forward-IV history.

Layout lives in ``scripts/iv_tenor_report.py`` (pull-only deploys). This loads it, builds,
and pushes to Telegram. Schedule: after the 17:05 iv_tenor_snapshot collector, ~17:20 ET. Descriptor only (rule 4).

Manual run:
    python -m trading_intel.scheduler.jobs.iv_tenor_report
"""
from __future__ import annotations

from trading_intel.scheduler.jobs._script_report import configure_logging, load_script, push


def run(session=None, settings=None, *, push_telegram: bool = True) -> str:
    from trading_intel.config import get_settings

    settings = settings or get_settings()
    path = str(load_script("iv_tenor_report").build())
    if push_telegram:
        push(path, "Index forward-IV term history (1M/3M)", settings)
    return path


def main() -> None:
    configure_logging()
    print(f"iv_tenor_report written: {run()}")


if __name__ == "__main__":
    main()
