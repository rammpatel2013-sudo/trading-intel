"""Focus names: write the night-before levels card for today's and the next day's picks (2026-11-20).

Reads only the database (focus_names, the chain bank, bars_1m) and writes the page file
``scripts/cvxmap/data/focus_card.json`` that the cockpit shows under a focus name. The file is a view of the
database (rebuildable any time), not a store.

Runs: DSM 16:30 weekdays (after the 16:05 chain bank run, so picks saved during the day get the final bank); the cockpit
server also runs it in the background right after a pick list is saved.
    python -m trading_intel.scheduler.jobs.focus_card [--symbols NVDA,AMD]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import structlog

from trading_intel import focus
from trading_intel.config import get_settings
from trading_intel.focus import card
from trading_intel.timeutils import eastern_now

log = structlog.get_logger(__name__)
COCKPIT_DATA = Path(__file__).resolve().parents[3] / "scripts" / "cvxmap" / "data"


def run(session, symbols: list[str] | None = None, out_dir: Path | None = None, now=None) -> dict:  # noqa: ANN001
    now = now or eastern_now()
    st = focus.state(session, now)
    syms = symbols or list(dict.fromkeys([*st.get("today_picks", []), *st.get("edit", [])]))
    cards = {}
    for s in syms:
        try:
            cards[s] = card.build(session, s, now)
        except Exception as exc:  # noqa: BLE001 - one bad name never blanks the others
            cards[s] = {"symbol": s, "error": str(exc)[:200]}
            log.warning("focus_card.name_failed", symbol=s, error=str(exc)[:200])
    out = {"generated": now.isoformat(timespec="seconds"), "picks": st, "cards": cards}
    d = Path(out_dir or os.environ.get("DASH_DIR") or COCKPIT_DATA)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "focus_card.json.tmp"
    tmp.write_text(json.dumps(out, separators=(",", ":"), default=str), encoding="utf-8")
    os.replace(tmp, d / "focus_card.json")
    log.info("focus_card.done", names=len(cards))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="focus_card")
    ap.add_argument("--symbols", help="comma list instead of the saved picks")
    a = ap.parse_args(argv)
    from trading_intel.memory.db import make_session_factory

    with make_session_factory(get_settings())() as s:
        res = run(s, [x.strip().upper() for x in a.symbols.split(",")] if a.symbols else None)
    print(json.dumps({k: len(v) if isinstance(v, dict) else v for k, v in res.items() if k != "picks"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
