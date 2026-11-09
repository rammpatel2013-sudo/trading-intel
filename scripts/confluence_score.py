"""Forward test of the SPX confluence read-out — score stored days against later bars.

For every stored ``reports/confluence/confluence_YYYYMMDD.json``, checks the next 1 and
5 sessions of SPX daily bars (``quotes_daily``): touched? held (closed back on the
original side) or broke? Compares key zones (3+ layers) with single-layer levels.
Writes ``reports/confluence/score.json`` and re-renders the latest day's HTML so its
"track record" section is current.

``--backfill`` first rebuilds past days from the per-strike history in ``oi_chain_eod``
(SPX books go back to ~2026-07-28; ``OI_CHAIN_RETENTION_DAYS`` = 365), oldest first, so
each day's rate-of-change compares with the day before. Existing files are kept unless
``--force``. Every input is read as of the rebuilt day; sources that do not reach back
that far (raw index tape kept 30 days, VIX options / vol surface from their first banked
day) leave that layer empty on those days and say so in the gaps — nothing is invented.

Track record of a descriptor, not a trading result (rule 4).

Run:
    python scripts/confluence_score.py
    python scripts/confluence_score.py --backfill [--since 2026-07-28] [--force]
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path


def backfill(session: object, out_dir: Path, *, since: date | None = None, force: bool = False,
             log: object = print) -> list[date]:
    from trading_intel.api import confluence as api

    done: list[date] = []
    for d in api.book_days(session, since=since):  # type: ignore[arg-type]
        if not force and api.read_json(d, out_dir) is not None:
            continue
        r = api.compute_for_day(session, d, out_dir=out_dir)  # type: ignore[arg-type]
        if r is None:
            log(f"skip {d}: no spot")  # type: ignore[operator]
            continue
        api.write_json(r, out_dir)
        done.append(d)
        log(f"built {d}: {len(r['key_zones'])} key zones")  # type: ignore[operator]
    return done


def score(session: object, out_dir: Path) -> dict:
    from trading_intel.api import confluence as api
    from trading_intel.market.confluence_score import score_days

    days = [api.read_json(d, out_dir) for d in api.stored_days(out_dir)]
    days = [d for d in days if d]
    bars = api._bars(session, api.SYMBOL, date.today(), n=600)  # type: ignore[arg-type]
    res = score_days(days, bars)
    res["n_days_stored"] = len(days)
    res["first_day"] = days[0]["as_of"] if days else None
    res["last_day"] = days[-1]["as_of"] if days else None
    p = out_dir / "score.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(res, indent=1), encoding="utf-8")
    return res


def run(session: object, out_dir: Path, *, do_backfill: bool = False, since: date | None = None,
        force: bool = False, html_out: bool = True, cockpit_json: Path | bool = True) -> dict:
    from trading_intel.api import confluence as api
    from trading_intel.market.confluence_score import headline

    if do_backfill:
        backfill(session, out_dir, since=since, force=force)
    res = score(session, out_dir)
    print(headline(res))
    days = api.stored_days(out_dir)
    if html_out and days:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_confluence_report", Path(__file__).resolve().parent / "confluence_report.py")
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        latest = api.read_json(days[-1], out_dir)
        if latest:
            print("html:", mod.write_html(latest, out_dir))
            if cockpit_json is not False:
                bars = api._bars(session, api.SYMBOL, days[-1], n=80)  # type: ignore[arg-type]
                path = cockpit_json if isinstance(cockpit_json, Path) else None
                print("cockpit:", mod.write_cockpit(latest, out_dir, bars, path))
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description="Score stored SPX confluence read-outs.")
    ap.add_argument("--backfill", action="store_true", help="rebuild past days from oi_chain_eod")
    ap.add_argument("--since", default=None, help="YYYY-MM-DD first day for --backfill")
    ap.add_argument("--force", action="store_true", help="overwrite stored days on --backfill")
    ap.add_argument("--no-html", action="store_true")
    ap.add_argument("--out-dir", default=None)
    a = ap.parse_args()
    from trading_intel.api import confluence as api
    from trading_intel.config import get_settings
    from trading_intel.memory.db import make_session_factory

    out = Path(a.out_dir) if a.out_dir else api.OUT_DIR
    with make_session_factory(get_settings())() as s:
        run(s, out, do_backfill=a.backfill, since=date.fromisoformat(a.since) if a.since else None,
            force=a.force, html_out=not a.no_html)


if __name__ == "__main__":
    main()
