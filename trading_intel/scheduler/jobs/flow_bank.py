"""Flow bank - keep the flow data we pay for but did not save (2026-11-19).

Two NAS DSM tasks, Mon-Fri (user root):
    16:25 ET  run_job.sh flow_bank                  (steps und, flowchart, bars_1m)
    17:25 ET  run_job.sh flow_bank --steps od_flow  (after od_nightly 16:20 wrote its files)
1. Convex ``und``: every name with option premium, ~50 fields, ONE call, plus the index rows by name
   (SPX VIX NDX RUT XSP) -> ``flow_und_daily``. Index counters were already 0 by 17:58 ET on
   2026-11-19 (stocks reset ~18:00), so this runs at 16:25.
2. Convex ``flowchart``: 15-second flow series for ``FLOWCHART_SYMBOLS`` -> ``flowchart_15s``.
   Convex serves TODAY only, so a missed day is lost. One call per symbol, paced 7 s (10/min cap).
3. optionsdata.ai flow categories (regular / spread / eth / eth_spread x buy / sell / undecided)
   from the files ``od_nightly`` wrote at 16:20 -> ``od_flow_daily``.
4. FMP 1-minute bars via CVForge for ``BAR_SYMBOLS`` -> ``bars_1m``.
Each step runs on its own; a failed step is logged and the job exits non-zero at the end.
Idempotent upserts. Descriptors only (rule 4); no AI (rule 7).

Load a saved day instead of calling (JSON files from a manual pull):
    python -m trading_intel.scheduler.jobs.flow_bank --import-dir <folder> --day 2026-11-19
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
import uuid
from datetime import date, datetime
from pathlib import Path

import structlog
from sqlalchemy.orm import Session

from trading_intel.config import Settings, get_settings
from trading_intel.flow import bank
from trading_intel.memory.models_flow import FLOWCHART_COLS, UND_FIELDS
from trading_intel.timeutils import eastern_now, is_trading_session

log = structlog.get_logger(__name__)

FLOWCHART_SYMBOLS = (
    "SPX",
    "SPY",
    "QQQ",
    "IWM",
    "VIX",
    "NDX",
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "GOOGL",
    "META",
    "TSLA",
)
BAR_SYMBOLS = (
    "SPY",
    "QQQ",
    "IWM",
    "DIA",
    "VOO",
    "VV",
    "SMH",
    "TLT",
    "HYG",
    "GLD",
    "XLK",
    "XLF",
    "XLE",
    "XLV",
    "XLI",
    "XLY",
    "XLP",
    "XLU",
    "XLB",
    "XLRE",
    "XLC",
)
INDEX_UND = ("SPX", "VIX", "NDX", "RUT", "XSP")
OD_DIR = Path("reports") / "optionsdata"
STEPS = ("und", "flowchart", "od_flow", "bars_1m")
CONVEX_PACE_S = 7.0


def _step(name: str, fn, out: dict, errors: list) -> None:  # noqa: ANN001
    try:
        out[name] = fn()
    except Exception as exc:
        errors.append(f"{name}: {type(exc).__name__}: {str(exc)[:200]}")
        out[name] = "failed"


def run(
    session: Session,
    settings: Settings | None = None,
    *,
    app: object | None = None,
    cvforge: object | None = None,
    day: date | None = None,
    now: datetime | None = None,
    force: bool = False,
    steps: tuple[str, ...] = ("und", "flowchart", "bars_1m"),
    sleep=time.sleep,  # noqa: ANN001
) -> dict:
    settings = settings or get_settings()
    now = now or eastern_now()
    day = day or now.date()
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="flow_bank")
    if not force and not is_trading_session(day):
        bound.info("flow_bank.skipped_non_session", day=day.isoformat())
        return {"skipped": "not_a_trading_session"}
    if now.hour >= 18 and not force and {"und", "flowchart"} & set(steps):
        bound.warning("flow_bank.after_reset", hint="Convex buy/sell counters reset ~18:00 ET")
    if app is None and {"und", "flowchart"} & set(steps):
        from trading_intel.clients.convex_app import ConvexAppClient

        app = ConvexAppClient(settings)
    if cvforge is None and "bars_1m" in steps:
        from trading_intel.clients.cvforge import CVForgeClient

        cvforge = CVForgeClient(settings)

    out: dict = {"day": day.isoformat()}
    errors: list[str] = []
    stamp = now.replace(tzinfo=None, microsecond=0)

    def und() -> dict:
        rows = bank.parse_und(app.und_flow(UND_FIELDS))  # type: ignore[attr-defined]
        sleep(CONVEX_PACE_S)
        idx = bank.parse_und(app.und_get(INDEX_UND, UND_FIELDS))  # type: ignore[attr-defined]
        rows = bank.merge_und(rows, idx)
        if not rows:
            raise RuntimeError("und returned 0 rows")
        ok = bank.split_ok(rows)
        if not ok:
            raise RuntimeError("buy/sell counters all 0 (after the reset?) - not saved")
        zero_idx = [r["symbol"] for r in idx if not (r.get("value") or 0)]
        return {"rows": bank.save_und(session, day, rows, stamp), "index_zero": zero_idx}

    def flowchart() -> dict:
        res, bad = {}, []
        for sym in FLOWCHART_SYMBOLS:
            sleep(CONVEX_PACE_S)
            try:
                rows = bank.parse_flowchart(app.flowchart(sym, d=day, cols=FLOWCHART_COLS))  # type: ignore[attr-defined]
            except Exception as exc:
                bad.append(f"{sym}: {str(exc)[:80]}")
                continue
            rows = [r for r in rows if r["trade_date"] == day]
            res[sym] = bank.save_flowchart(session, rows) if rows else 0
        empty = [s for s, n in res.items() if not n]
        if bad or len(empty) == len(FLOWCHART_SYMBOLS):
            raise RuntimeError(f"flowchart failed {bad} empty {empty}")
        return res

    def od_flow() -> dict:
        folder = OD_DIR / f"{day:%Y%m%d}"
        if not folder.is_dir():
            raise RuntimeError(f"no od_nightly folder {folder}")
        rows = bank.od_flow_from_dir(day, folder)
        if not rows:
            raise RuntimeError(f"no flow rows in {folder}")
        return {"rows": bank.save_od_flow(session, rows)}

    def bars() -> dict:
        res, bad = {}, []
        for sym in BAR_SYMBOLS:
            try:
                raw = cvforge.fmp(  # type: ignore[attr-defined]
                    "historical-chart/1min",
                    {"symbol": sym, "from": day.isoformat(), "to": day.isoformat()},
                )
            except Exception as exc:
                bad.append(f"{sym}: {str(exc)[:60]}")
                continue
            rows = [r for r in bank.parse_fmp_1m(sym, raw) if r["ts"].date() == day]
            res[sym] = bank.save_bars_1m(session, rows) if rows else 0
        if bad or not any(res.values()):
            raise RuntimeError(f"bars failed {bad}")
        return res

    fns = {"und": und, "flowchart": flowchart, "od_flow": od_flow, "bars_1m": bars}
    for name in steps:
        _step(name, fns[name], out, errors)
    out["errors"] = errors
    try:
        from trading_intel.scheduler import track

        track.report(rows_attempted=len(steps), rows_written=len(steps) - len(errors))
    except Exception:  # noqa: S110 - bookkeeping only
        pass
    (bound.warning if errors else bound.info)("flow_bank.done", **out)
    if errors:
        raise RuntimeError("flow_bank: " + " | ".join(errors))
    return out


def import_dir(session: Session, folder: Path, day: date) -> dict:
    """Load JSON saved by a manual pull.

    Files: ``und_<YYYYMMDD>.json.gz`` + ``flowchart_<SYM>_<YYYYMMDD>.json.gz``."""
    out: dict = {}
    und = folder / f"und_{day:%Y%m%d}.json.gz"
    if und.exists():
        resp = json.load(gzip.open(und, "rt"))
        fields = [f for f in resp.get("params", []) if f != "symbol"] or list(UND_FIELDS)
        rows = bank.parse_und(resp, fields)
        keep = set(UND_FIELDS)
        rows = [{k: v for k, v in r.items() if k == "symbol" or k in keep} for r in rows]
        stamp = datetime.fromtimestamp(und.stat().st_mtime).replace(microsecond=0)
        out["und"] = bank.save_und(session, day, rows, stamp)
    for f in sorted(folder.glob(f"flowchart_*_{day:%Y%m%d}.json.gz")):
        rows = [
            r for r in bank.parse_flowchart(json.load(gzip.open(f, "rt"))) if r["trade_date"] == day
        ]
        out[f.name.split("_")[1]] = bank.save_flowchart(session, rows)
    return out


def main(argv: list[str] | None = None) -> int:
    from trading_intel.memory.db import make_session_factory

    ap = argparse.ArgumentParser(prog="flow_bank")
    ap.add_argument("--day")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--import-dir")
    ap.add_argument("--steps", default="und,flowchart,bars_1m", help=f"comma list of {STEPS}")
    a = ap.parse_args(argv)
    s = get_settings()
    day = date.fromisoformat(a.day) if a.day else None
    with make_session_factory(s)() as session:
        if a.import_dir:
            print(json.dumps(import_dir(session, Path(a.import_dir), day or eastern_now().date())))
            return 0
        steps = tuple(x for x in a.steps.split(",") if x)
        bad = [x for x in steps if x not in STEPS]
        if bad:
            ap.error(f"unknown steps {bad}")
        run(session, s, day=day, force=a.force, steps=steps)
    return 0


if __name__ == "__main__":
    sys.exit(main())
