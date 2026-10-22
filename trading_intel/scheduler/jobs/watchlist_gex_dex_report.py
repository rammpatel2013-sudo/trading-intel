"""DSM job wrapper: watchlist-wide rolling Net GEX + Net DEX.

Layout lives in ``scripts/watchlist_gex_dex_report.py`` (pull-only deploys). This loads it, builds,
and pushes to Telegram. Schedule: weekly is plenty (~1MB file), e.g. Fri 18:20 ET. Descriptor only (rule 4).

Manual run:
    python -m trading_intel.scheduler.jobs.watchlist_gex_dex_report
"""
from __future__ import annotations

from trading_intel.scheduler.jobs._script_report import configure_logging, load_script, push


def run(session=None, settings=None, *, push_telegram: bool = True) -> str:
    from trading_intel.config import get_settings

    settings = settings or get_settings()
    path = str(load_script("watchlist_gex_dex_report").build())
    if push_telegram:
        push(path, "Watchlist rolling Net GEX / DEX", settings)
    return path


def main() -> None:
    configure_logging()
    print(f"watchlist_gex_dex_report written: {run()}")


if __name__ == "__main__":
    main()
