"""Stage breadth job (2026-11-22) - NAS: chained after "TI equity daily" (Mon-Fri 18:45, which brings in today's
S&P 1500 bars): ``run_job.sh equity_daily; run_job.sh stage_breadth; run_script.sh risk_page``.

1. today's bars for the 11 sector funds (XLK ... XLU; ~11 CVForge FMP calls; first run back-fills since 2023)
2. stage of every S&P 1500 member per day (Candle Stage rules, ``equity/stages.py``) from equity_bars
3. ``equity_stages`` rows (daily stage + weekly stage + Mansfield RS): every day on the first run (empty table), else the last 10 sessions (idempotent upsert)
4. ``scripts/cvxmap/data/stagetab.json`` for the Risk page: stage mix, Stage 2 / Stage 4 share, net entries,
   stretched share, weight of evidence, today's breakouts / breakdowns, sector stage mix, sector rotation,
   weekly (30-week Weinstein) stage mix + new weekly Stage 2 / 4, industry groups by stage (equity/stage_groups.py)

Skips non-sessions. Descriptor only (rule 4) - nothing here writes ``signals``.
Manual: ``python -m trading_intel.scheduler.jobs.stage_breadth [--all]`` (``--all`` rewrites every day's rows).
"""

from __future__ import annotations

import argparse
import json
import uuid
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_intel.config import Settings, get_settings
from trading_intel.equity import pipeline as pl
from trading_intel.equity import stage_breadth as sb
from trading_intel.equity import store, universe
from trading_intel.equity.panel import load_panel
from trading_intel.equity.rotation import SECTOR_ETFS
from trading_intel.equity.stage_groups import daily_weekly
from trading_intel.equity.stages import classify
from trading_intel.memory.models_equity import EquityStage
from trading_intel.timeutils import eastern_now, is_trading_session

log = structlog.get_logger(__name__)
COCKPIT_DATA = Path(__file__).resolve().parents[3] / "scripts" / "cvxmap" / "data"
OUT = "stagetab.json"
REDO_DAYS = 10


def run(session: Session, settings: Settings | None = None, *, src: Any = None, day: date | None = None,  # noqa: ANN401
        rewrite_all: bool = False, out_dir: Path | None = None, refresh: bool = True) -> dict:
    settings = settings or get_settings()
    day = day or eastern_now().date()
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="stage_breadth")
    if not is_trading_session(day) and not rewrite_all:
        return {"skipped": "not_a_trading_session"}
    ivs = store.load_members(session)
    if not ivs:
        return {"skipped": "no_universe"}
    etfs = sorted(set(SECTOR_ETFS) | {"SPY"})
    if refresh:
        try:
            if src is None:
                from trading_intel.clients.equity_data import default_source

                src = default_source(settings)
            pl.refresh_bars(session, src, etfs, day)
        except Exception as exc:  # noqa: BLE001 - sector funds are context; the stage mix still builds
            bound.warning("stage_breadth.sector_refresh_failed", error=str(exc)[:200])
    profs = store.load_profiles(session)
    p = load_panel(session, pl.HISTORY_START, ivs, profs,
                   extra_symbols=tuple(etfs) + tuple(universe.BENCHMARKS))
    if not p.dates:
        return {"skipped": "no_bars"}
    stock_cols = [i for i, s in enumerate(p.symbols) if s not in set(etfs) | set(universe.BENCHMARKS)]
    cols = np.array(stock_cols, dtype=int)
    close, high, low = p.close[:, cols], p.high[:, cols], p.low[:, cols]
    member = p.member[:, cols]
    syms = [p.symbols[i] for i in cols]
    code, xatr = classify(close, high, low)
    extra = {s: p.close[:, p.col(s)] for s in etfs if p.col(s) is not None}
    o = sb.compute(p.dates, syms, close, high, low, member, [p.sector[i] for i in cols], p.mcap[:, cols], extra,
                   code=code, xatr=xatr, industry=[getattr(profs.get(s), "industry", None) for s in syms])
    have = session.execute(select(func.count()).select_from(EquityStage)).scalar() or 0
    since = None if (rewrite_all or have == 0) else p.dates[max(0, len(p.dates) - REDO_DAYS)]
    ws, mrs = daily_weekly(p.dates, close, extra.get("SPY"))
    rows = sb.stage_rows(p.dates, syms, code, xatr, member, since=since, wstage=ws, mrs=mrs)
    n = store.upsert(session, EquityStage, rows, ["date", "symbol"], chunk=2000)
    o["built"] = eastern_now().isoformat(timespec="seconds")
    out = (out_dir or COCKPIT_DATA) / OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(o, separators=(",", ":"), default=str), encoding="utf-8")
    tmp.replace(out)
    bound.info("stage_breadth.done", asof=o["asof"], members=o["n_members"], rows=n,
               stage2=o["cards"][0]["value"], stage4=o["cards"][1]["value"])
    return {"asof": o["asof"], "members": o["n_members"], "rows": n, "counts": o["counts"]}


def main() -> None:
    from trading_intel.memory.db import make_session_factory

    ap = argparse.ArgumentParser(description="stage breadth (Candle Stage rules on the S&P 1500)")
    ap.add_argument("--all", action="store_true", help="rewrite equity_stages for every day")
    a = ap.parse_args()
    s = get_settings()
    with make_session_factory(s)() as session:
        print(run(session, s, rewrite_all=a.all))


if __name__ == "__main__":
    main()

