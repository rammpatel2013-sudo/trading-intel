"""DSM job wrapper: on-demand swing-setup scanner over the watchlist (live CVForge).

Layout lives in ``scripts/swing_report.py`` (pull-only deploys). This loads it, builds,
and pushes to Telegram. Schedule: weekly, e.g. Sun 09:00 ET (CVForge only, no Convex budget). Descriptor only (rule 4).

Manual run:
    python -m trading_intel.scheduler.jobs.swing_report
"""
from __future__ import annotations

from trading_intel.scheduler.jobs._script_report import configure_logging, load_script, push


def run(session=None, settings=None, *, push_telegram: bool = True) -> str:
    from trading_intel.config import get_settings

    settings = settings or get_settings()
    path = str(load_script("swing_report").build(settings=settings))
    if push_telegram:
        push(path, "Swing setups — watchlist scan", settings)
    return path


def main() -> None:
    configure_logging()
    print(f"swing_report written: {run()}")


if __name__ == "__main__":
    main()
