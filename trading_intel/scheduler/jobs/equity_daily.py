"""Equity / swing daily job (E1-E6) - NAS DSM Mon-Fri 18:45 ET: ``run_job.sh equity_daily``.

1. new daily bars for today's S&P 1500 members + benchmarks (FMP via CVForge, ~1,500 calls at 8/s)
2. NYSE / Nasdaq new highs - new lows file (thetrading.tools, one GET, ADR-009)
3. factor model + gate + candidate lists (``equity.pipeline.compute``)
4. option checks on the shortlist (CVForge chains: 90-180 DTE call IV banked, debit-spread card)
5. the page on the cockpit server: ``scripts/cvxmap/data/equity.html`` -> http://192.168.1.211:8899/equity.html
   ([stated] 2026-11-14 12:49 "i dont need it on telegram. just mcp tool and cockpit")

Skips non-sessions and a stale vendor day (SPY's newest bar must be today). Idempotent (rule 5);
descriptor only (rule 4). Manual: ``python -m trading_intel.scheduler.jobs.equity_daily``.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_intel.config import Settings, get_settings
from trading_intel.equity import candidates as cand
from trading_intel.equity import pipeline as pl
from trading_intel.equity import report, risk, store, universe
from trading_intel.equity.styles import STYLES
from trading_intel.memory.models_equity import EquityBar, EquityGateDaily
from trading_intel.timeutils import eastern_now, is_trading_session

log = structlog.get_logger(__name__)
# cvxmap-serve serves scripts/cvxmap/data (run_job.sh mounts the host scripts/ at /app/scripts)
COCKPIT_DATA = Path(__file__).resolve().parents[3] / "scripts" / "cvxmap" / "data"
PAGE = "equity.html"


def cards(p: Any, rows: list[dict], checks: dict[str, dict]) -> dict[str, dict]:  # noqa: ANN401
    """Stops, size and the option card per candidate."""
    out: dict[str, dict] = {}
    for r in rows:
        s = r["symbol"]
        i = p.col(s)
        if i is None or s in out:
            continue
        c = p.close[:, i]
        a = risk.atr(p.high[-30:, i], p.low[-30:, i], c[-30:])
        sma50 = float(np.nanmean(c[-50:])) if len(c) >= 50 else None
        st = risk.stops(float(c[-1]), a, sma50)
        ck = checks.get(s, {})
        out[s] = {
            "stops": st,
            "size": risk.position_size(float(c[-1]), st.get("stop")),
            "spread": ck.get("spread"),
            "call_bid": ck.get("call_bid"),
        }
        if ck.get("spread"):
            out[s]["contracts"] = risk.spread_contracts(ck["spread"]["debit"])
    return out


def series(p: Any, rows: list[dict], n: int = 126) -> dict[str, list[float | None]]:  # noqa: ANN401
    """Last ``n`` closes per candidate for the report's mini charts."""
    out: dict[str, list[float | None]] = {}
    for r in rows:
        i = p.col(r["symbol"])
        if i is not None and r["symbol"] not in out:
            out[r["symbol"]] = [None if np.isnan(x) else float(x) for x in p.close[-n:, i]]
    return out


def buckets(p: Any, lookback: int = 20) -> dict[str, list[float]] | None:  # noqa: ANN401
    """Share of stocks in each drawdown bucket today and ``lookback`` sessions ago."""
    from trading_intel.equity import damage

    sh = damage.damage_stats(p)["bucket_shares"]
    if len(sh) <= lookback or np.isnan(sh[-1]).any() or np.isnan(sh[-1 - lookback]).any():
        return None
    return {"now": [float(x) for x in sh[-1]], "then": [float(x) for x in sh[-1 - lookback]]}


def tides(run: Any, n: int = 20) -> list[tuple[str, float]]:  # noqa: ANN401
    fits = [f for f in run.fits[-n:] if f is not None]
    return [(s, round(sum(f.factors.get(s, 0.0) for f in fits) * 100, 2)) for s in STYLES]


def run(
    session: Session,
    settings: Settings | None = None,
    *,
    src: Any = None,
    cv: Any = None,  # noqa: ANN401
    day: date | None = None,
    page_dir: Path | None = None,
) -> dict:
    settings = settings or get_settings()
    day = day or eastern_now().date()
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="equity_daily")
    if not is_trading_session(day):
        return {"skipped": "not_a_trading_session"}
    ivs = store.load_members(session)
    if not ivs:
        bound.error("equity_daily.no_universe", hint="run scripts/equity_backfill.py first")
        return {"skipped": "no_universe"}
    if src is None:
        from trading_intel.clients.equity_data import default_source

        src = default_source(settings)
    current = sorted({iv.symbol for iv in ivs if iv.end_date is None} | set(universe.BENCHMARKS))
    pl.refresh_bars(session, src, current, day)
    newest = session.execute(
        select(func.max(EquityBar.date)).where(EquityBar.symbol == "SPY")
    ).scalar()
    if newest != day:
        bound.error("equity_daily.source_stale", newest=str(newest), day=str(day))
        return {"skipped": "source_stale", "newest": str(newest)}
    pl.refresh_nhnl(session, day, since=day - timedelta(days=60))
    res = pl.compute(session, day)
    if "skipped" in res:
        return res
    p, run_ = res["panel"], res["run"]
    names = cand.shortlist(res["candidates"])
    if cv is None:
        from trading_intel.clients.cvforge import CVForgeClient

        cv = CVForgeClient(settings)
    checks = pl.option_checks(session, cv, names, day)
    hist = [
        {"date": g.date, "median_drawdown": g.median_drawdown, "repair_flow": g.repair_flow,
         "color": g.color}
        for g in session.execute(
            select(EquityGateDaily)
            .where(EquityGateDaily.date >= day - timedelta(days=370))
            .order_by(EquityGateDaily.date)
        ).scalars()
    ]
    data = {
        "day": res["day"],
        "gate": res["gate"],
        "gate_history": hist,
        "tides": tides(run_),
        "candidates": res["candidates"],
        "cards": cards(p, res["candidates"], checks),
        "series": series(p, res["candidates"]),
        "buckets": buckets(p),
    }
    target = page_dir or COCKPIT_DATA
    if not target.is_dir():
        bound.warning("equity_daily.no_cockpit_folder", folder=str(target))
        return {"day": str(res["day"]), "gate": res["gate"], "page": None}
    out = target / PAGE
    tmp = out.with_suffix(".tmp")
    tmp.write_text(report.build_html(data), encoding="utf-8")
    tmp.replace(out)  # swap in whole, so the server never serves half a page
    bound.info("equity_daily.done", gate=(res["gate"] or {}).get("color"), page=str(out),
               shortlist=len(names))
    return {"day": str(res["day"]), "gate": res["gate"], "page": str(out)}

def main() -> None:
    from trading_intel.memory.db import make_session_factory

    s = get_settings()
    with make_session_factory(s)() as session:
        print(run(session, s))


if __name__ == "__main__":
    main()
