"""Desk brief - build the evening brief (2026-11-14). NAS DSM Mon-Fri 19:00 ET (``run_job.sh desk_brief``).

Runs after finra_dix (18:30, now keeps every symbol's FINRA row) and equity_daily (18:45, S&P 1500
bars). Steps: names (FMP company screener, weekly) -> universe (optionable + $5 + $10M a day) ->
bars for names outside the S&P 1500 -> short-dated gamma (one CVForge SQL) -> build -> top-pick
card -> our picks -> scorecard (ours + TailThatWagsDog's) -> desk_brief_daily row + the page
http://192.168.1.211:8899/desk_brief.html. No Telegram. Descriptor only (rule 4); no AI (rule 7).
"""

from __future__ import annotations

import json
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy.orm import Session

from trading_intel.config import Settings, get_settings
from trading_intel.desk import build, report, score, store
from trading_intel.desk import fundamentals as fund
from trading_intel.equity.parse import bar_rows
from trading_intel.timeutils import eastern_now, is_trading_session

log = structlog.get_logger(__name__)
COCKPIT_DATA = Path(__file__).resolve().parents[3] / "scripts" / "cvxmap" / "data"
PAGE = "desk_brief.html"
ST_DAYS = 14  # "short-term" gamma window in calendar days (his window is not public)
NAMES_MAX_AGE = 7


def refresh_names(
    session: Session, src: Any, today: date, *, force: bool = False
) -> int:  # noqa: ANN401
    last = store.names_updated(session)
    if not force and last and (today - last).days < NAMES_MAX_AGE:
        return 0
    rows: dict[str, dict] = {}
    for etf in ("false", "true"):
        try:
            data = src._fmp(
                "company-screener", {"isEtf": etf, "isActivelyTrading": "true", "limit": 10000}
            )
        except Exception as exc:  # stale names are better than none
            log.warning("desk_brief.names_failed", is_etf=etf, error=str(exc)[:160])
            continue
        for r in data if isinstance(data, list) else []:
            s = str(r.get("symbol") or "").upper()
            if s and len(s) <= 16 and "." not in s:
                rows[s] = {
                    "symbol": s,
                    "name": (r.get("companyName") or "")[:160],
                    "sector": r.get("sector"),
                    "industry": (r.get("industry") or "")[:96],
                    "is_etf": bool(r.get("isEtf")),
                    "avg_volume": r.get("avgVolume") or r.get("volume"),
                    "price": r.get("price"),
                    "market_cap": r.get("marketCap"),
                    "beta": r.get("beta"),
                    "updated": today,
                }
    return store.save_names(session, list(rows.values())) if rows else 0


def universe(session: Session, day: date) -> list[str]:
    """Optionable names (latest und pull) that clear $5 and $10M average dollar volume."""
    names = store.load_names(session)
    dates = store.und_dates(session, day, 1)
    und = store.load_und(session, dates[0]) if dates else {}
    out = []
    for s, u in und.items():
        n = names.get(s)
        if not n or not (u.option_volume or 0):
            continue
        px, av = n.get("price") or 0, n.get("avg_volume") or 0
        if px >= build.PRICE_MIN and px * av >= build.DVOL_MIN:
            out.append(s)
    return sorted(set(out) | set(build.BACKDROP))


def refresh_bars(session: Session, src: Any, syms: list[str], day: date) -> int:  # noqa: ANN401
    """Bars for names outside equity_bars (and equity names whose bar for ``day`` is missing)."""
    have = store.load_bars(session, syms, day - timedelta(days=10), day)
    last = store.last_desk_bar_dates(session)
    need = {}
    for s in syms:
        b = have.get(s) or []
        if b and b[-1][0] >= day and len(b) >= 4:
            continue
        lt = last.get(s)
        need[s] = (lt - timedelta(days=7)) if lt else (day - timedelta(days=60))
    if not need:
        return 0
    got = src.fetch_many(list(need), lambda s: src.bars(s, need[s].isoformat()), label="desk_bars")
    rows = [r for s, raw in got.items() for r in bar_rows(s, raw or [])]
    return store.save_bars(session, rows)


def refresh_gamma(session: Session, cv: Any, day: date) -> int:  # noqa: ANN401
    df = cv.gex_by_underlying(day, day + timedelta(days=ST_DAYS))
    return store.save_gamma(session, day, df.to_dict("records") if len(df) else [], ST_DAYS)


def scorecard(session: Session, day: date) -> dict:
    picks = [
        {"date": p.date, "source": p.source, "symbol": p.symbol, "role": p.role}
        for p in store.load_picks(session)
        if p.date <= day and p.date >= day - timedelta(days=60)
    ]
    if not picks:
        return {"stats": {}, "open": []}
    start = min(p["date"] for p in picks) - timedelta(days=3)
    bars = store.load_bars(session, {p["symbol"] for p in picks} | {"SPY"}, start)
    return score.scorecard(picks, bars, bars.get("SPY", []))


def our_picks(p: dict) -> list[tuple[str, str, dict | None]]:
    out: list[tuple[str, str, dict | None]] = []
    if p.get("top_pick"):
        out.append((p["top_pick"]["symbol"], "top", {"why": p["top_pick"].get("why")}))
    out += [(r["symbol"], "two_leg", None) for r in p.get("two_leg") or []]
    out += [(s, "div_a", None) for s in p.get("div_a") or []]
    out += [(s, "div_b", None) for s in p.get("div_b") or []]
    out += [(r["symbol"], "screen", None) for r in p.get("screen") or []]
    return out


def run(
    session: Session,
    settings: Settings | None = None,
    *,
    cv: Any = None,
    src: Any = None,  # noqa: ANN401
    day: date | None = None,
    page_dir: Path | None = None,
    fetch: bool = True,
    force: bool = False,
) -> dict:
    settings = settings or get_settings()
    day = day or eastern_now().date()
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="desk_brief", day=str(day))
    if not force and not is_trading_session(day):
        return {"skipped": "not_a_trading_session"}
    if fetch:
        if cv is None:
            from trading_intel.clients.cvforge import CVForgeClient

            cv = CVForgeClient(settings)
        if src is None:
            from trading_intel.clients.equity_data import EquityDataSource

            src = EquityDataSource(cv)
        refresh_names(session, src, day)
    syms = universe(session, day)
    if not syms or len(syms) <= len(build.BACKDROP):
        bound.warning(
            "desk_brief.no_universe",
            hint="no desk_und_daily rows - did desk_snapshot run at 17:15?",
        )
        return {"skipped": "no_universe"}
    if fetch:
        recent = {p.symbol for p in store.load_picks(session) if p.date >= day - timedelta(days=40)}
        n_bars = refresh_bars(session, src, sorted(set(syms) | recent | {"SPY"}), day)
        try:
            n_gam = refresh_gamma(session, cv, day)
        except Exception as exc:  # brief still builds; gamma column shows "-"
            bound.warning("desk_brief.gamma_failed", error=str(exc)[:160])
            n_gam = 0
        bound.info("desk_brief.fetched", universe=len(syms), bars=n_bars, gamma=n_gam)
    payload = build.build(build.gather(session, day, universe=syms))
    card = hw = None
    if payload.get("top_pick") and src is not None:
        card = fund.top_card(src, payload["top_pick"]["symbol"], day)
        hw = fund.hold_weaken(payload["top_pick"], card)
    if payload["coverage"].get("n_screen"):
        store.save_picks(session, day, "ours", our_picks(payload))
    sc = scorecard(session, day)
    payload.update({"top_card": card, "hold_weaken": hw, "score": sc})
    payload = json.loads(
        json.dumps(payload, default=str)
    )  # JSON-safe (dates, numpy) before storing
    store.save_brief(session, day, payload)
    out_dir = page_dir or COCKPIT_DATA
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / (PAGE + ".tmp")
    tmp.write_text(report.build_html(payload, card=card, hw=hw, score=sc), encoding="utf-8")
    tmp.replace(out_dir / PAGE)
    res = {
        "day": day.isoformat(),
        "screen": payload["coverage"].get("n_screen"),
        "two_leg": len(payload.get("two_leg") or []),
        "top_pick": (payload.get("top_pick") or {}).get("symbol"),
        "coverage": payload["coverage"],
        "page": str(out_dir / PAGE),
    }
    bound.info("desk_brief.done", **{k: v for k, v in res.items() if k != "coverage"})
    return res


def main() -> None:
    import argparse

    from trading_intel.memory.db import make_session_factory

    ap = argparse.ArgumentParser()
    ap.add_argument("--day", help="YYYY-MM-DD (default today ET)")
    ap.add_argument("--force", action="store_true", help="run even on a non-session day")
    a = ap.parse_args()
    s = get_settings()
    with make_session_factory(s)() as session:
        print(run(session, s, day=date.fromisoformat(a.day) if a.day else None, force=a.force))


if __name__ == "__main__":
    main()
