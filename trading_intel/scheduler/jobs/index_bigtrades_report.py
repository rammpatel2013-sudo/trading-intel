"""DSM job wrapper: index big-trades (live tape, grouped into structures).

Layout lives in ``scripts/index_bigtrades_report.py`` (pull-only deploys). This loads it, builds,
and pushes to Telegram. Schedule: RTH ONLY — the tape zeroes after 16:00 ET; e.g. 15:45 ET. Descriptor only (rule 4).

Manual run:
    python -m trading_intel.scheduler.jobs.index_bigtrades_report
"""
from __future__ import annotations

from trading_intel.scheduler.jobs._script_report import configure_logging, load_script, push


def run(session=None, settings=None, *, push_telegram: bool = True) -> str:
    from trading_intel.config import get_settings

    settings = settings or get_settings()
    path = str(load_script("index_bigtrades_report").build(settings=settings))
    if push_telegram:
        push(path, "Index big trades — by structure (live tape)", settings)
    return path


def main() -> None:
    configure_logging()
    print(f"index_bigtrades_report written: {run()}")


if __name__ == "__main__":
    main()
