"""DSM job wrapper: weekly 9-gate swing scorecard -> Telegram (Phase 5 P5, 2026-11-04).

Layout lives in ``scripts/swing_scorecard.py`` (pull-only deploys). Reads banked
``swing_features`` + ``earnings_events`` only. Schedule: Sat ~09:10 ET, after the
weekday swing_features collector. Descriptor only (rule 4).

Manual run:
    python -m trading_intel.scheduler.jobs.swing_scorecard_report
"""
from __future__ import annotations

from trading_intel.scheduler.jobs._script_report import configure_logging, load_script, push


def run(session=None, settings=None, *, push_telegram: bool = True) -> str:
    from trading_intel.config import get_settings

    settings = settings or get_settings()
    path, summary = load_script("swing_scorecard").build(settings=settings)
    flagged = [s["symbol"] for s in summary if s["flagged"]]
    if push_telegram:
        cap = "Swing scorecard (9 gates) — flagged: " + (", ".join(flagged[:12]) or "none")
        push(str(path), cap, settings)
    return str(path)


def main() -> None:
    configure_logging()
    print(f"swing_scorecard written: {run()}")


if __name__ == "__main__":
    main()
