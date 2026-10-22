"""DSM job wrapper: SPX/SPY dealer positioning (gamma/charm/vanna/vega, now vs prior book).

Layout lives in ``scripts/dealer_positioning_report.py`` (pull-only deploys).
Schedule after the 08:35 ``index_walls_am`` snapshot, e.g. 08:50 ET weekdays.
Descriptor only (rule 4).

Manual run:
    python -m trading_intel.scheduler.jobs.dealer_positioning_report
"""
from __future__ import annotations

from trading_intel.scheduler.jobs._script_report import configure_logging, load_script, push


def run(session=None, settings=None, *, push_telegram: bool = True) -> str:
    from trading_intel.config import get_settings

    settings = settings or get_settings()
    path = load_script("dealer_positioning_report").build(settings=settings, session=session)
    if push_telegram:
        push(path, "Dealer positioning — SPX / SPY (now vs prior book)", settings)
    return path


def main() -> None:
    configure_logging()
    print(f"dealer_positioning_report written: {run()}")


if __name__ == "__main__":
    main()
