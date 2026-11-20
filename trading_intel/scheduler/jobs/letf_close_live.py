"""Leveraged-ETF close flow, live (2026-11-19) - NAS DSM Mon-Fri 11:58 ET: ``run_job.sh letf_close_live``.

[stated] 2026-11-19 18:52 "no testing, need to start doing something ... even its 5-10 min delay its still
imp and very close approximation". The job sleeps until 60 min before the session close (16:00, or 13:00 on
a half day), then every 2 minutes until 6 min after the close:

1. shares outstanding per fund: today's ``letf_shares_snapshots`` row, else one CVForge FMP call per fund
   (``shares-float``) written to that table first (first pass of the day only).
2. 1-minute bars for every S&P 500 / Nasdaq-100 leveraged + inverse fund and the two indexes
   (``^GSPC`` -> SPX, ``^NDX`` -> NDX) from FMP via CVForge -> ``bars_1m`` (source ``fmp``).
3. reads them back FROM THE DB and computes the forced rebalance (``etf_flows/close_flow.py``) ->
   ``letf_close_flow`` (one row per index per pass).
4. the page ``scripts/cvxmap/data/close_flow.html`` -> http://192.168.1.211:8899/close_flow.html
   (+ ``close_flow.json``) and ONE Telegram line at the first pass from 15 min before the close.

One database rule ([stated] 2026-11-18): vendor -> DB -> calc; the calc and the page read only the DB.
Manual: ``python -m trading_intel.scheduler.jobs.letf_close_live --once`` (one pass now, no wait).
Descriptor only (rule 4). No AI (rule 7).
"""

from __future__ import annotations

import argparse
import json
import sys
import time as _time
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.equity.store import upsert
from trading_intel.etf_flows import close_flow as cf
from trading_intel.etf_flows import close_flow_page as page
from trading_intel.flow import bank
from trading_intel.memory.models import LetfSharesSnapshot, QuoteDaily
from trading_intel.memory.models_close_flow import LetfCloseFlow
from trading_intel.memory.models_flow import Bars1m
from trading_intel.timeutils import eastern_now, is_trading_session

log = structlog.get_logger(__name__)
COCKPIT_DATA = Path(__file__).resolve().parents[3] / "scripts" / "cvxmap" / "data"
PAGE = "close_flow.html"
JSON_OUT = "close_flow.json"
INTERVAL_S = 120
LEAD = timedelta(minutes=60)
TAIL = timedelta(minutes=6)
TG_FROM = timedelta(minutes=15)  # Telegram once, at the first pass from close - 15 min


# ── 1. shares ────────────────────────────────────────────────────────────
def latest_shares(session: Session, symbols: Sequence[str], day: date) -> dict[str, tuple[date, float]]:
    q = select(LetfSharesSnapshot.symbol, LetfSharesSnapshot.ts, LetfSharesSnapshot.shares_outstanding).where(
        LetfSharesSnapshot.symbol.in_(list(symbols)), LetfSharesSnapshot.ts <= day
    )
    out: dict[str, tuple[date, float]] = {}
    for sym, ts, sh in session.execute(q).all():
        if sh and (sym not in out or ts > out[sym][0]):
            out[sym] = (ts, float(sh))
    return out


def ensure_shares(session: Session, cvf, symbols: Sequence[str], day: date, settings) -> dict[str, float]:  # noqa: ANN001
    have = latest_shares(session, symbols, day)
    need = [s for s in symbols if have.get(s, (None,))[0] != day]
    if need and cvf is not None:
        from trading_intel.scheduler.jobs import letf_flows

        rows = letf_flows.build_rows(cvf, settings, as_of=day, symbols=need)
        if rows:
            letf_flows._ensure_tickers(session, {r["symbol"] for r in rows})
            letf_flows._upsert(session, rows)
            session.commit()
        have = latest_shares(session, symbols, day)
    return {s: v[1] for s, v in have.items()}


# ── 2. bars ──────────────────────────────────────────────────────────────
def pull_bars(session: Session, cvf, fmp_sym: str, our_sym: str, frm: date, to: date) -> int:  # noqa: ANN001
    raw = cvf.fmp("historical-chart/1min", {"symbol": fmp_sym, "from": frm.isoformat(), "to": to.isoformat()})
    rows = [r for r in bank.parse_fmp_1m(our_sym, raw) if frm <= r["ts"].date() <= to]
    return bank.save_bars_1m(session, rows) if rows else 0


def bar_at(session: Session, sym: str, day: date, before: time) -> tuple[float, datetime] | None:
    """Last 1-minute bar of ``day`` that starts before ``before`` -> (close, ts)."""
    lo = datetime.combine(day, time(0, 0))
    hi = datetime.combine(day, before)
    q = (
        select(Bars1m.close, Bars1m.ts)
        .where(Bars1m.symbol == sym, Bars1m.ts >= lo, Bars1m.ts < hi, Bars1m.close.is_not(None))
        .order_by(Bars1m.ts.desc())
        .limit(1)
    )
    r = session.execute(q).first()
    return (float(r[0]), r[1]) if r else None


def has_day(session: Session, sym: str, day: date) -> bool:
    return bar_at(session, sym, day, time(23, 59)) is not None


def official_close(session: Session, sym: str, day: date) -> float | None:
    r = session.execute(
        select(QuoteDaily.close).where(QuoteDaily.symbol == sym, QuoteDaily.date == day)
    ).first()
    return float(r[0]) if r and r[0] else None


# ── 3. one pass ──────────────────────────────────────────────────────────
def one_pass(session: Session, cvf, now: datetime, settings=None) -> dict:  # noqa: ANN001
    day = now.date()
    prev = cf.prev_session(day, is_trading_session)
    close_t = cf.session_close(day)
    upto = min(now.time(), close_t)  # never read a bar from after the close
    groups = {g: cf.funds_for(g) for g in cf.GROUPS}
    fund_syms = [m.symbol for ms in groups.values() for m in ms]
    errors: list[str] = []

    shares = ensure_shares(session, cvf, fund_syms, day, settings)

    pulls = [(m.symbol, m.symbol) for m in (x for ms in groups.values() for x in ms)]
    pulls += [(cf.GROUPS[g]["fmp"], g) for g in cf.GROUPS]
    if cvf is not None:
        for fmp_sym, sym in pulls:
            frm = day if has_day(session, sym, prev) else prev
            try:
                pull_bars(session, cvf, fmp_sym, sym, frm, day)
            except Exception as exc:  # noqa: BLE001 - one symbol never stops the pass
                errors.append(f"{sym}: {str(exc)[:80]}")
        session.commit()

    # everything below reads the DB only
    prev_close_t = cf.session_close(prev)
    out_groups = []
    for g, metas in groups.items():
        funds = []
        for m in metas:
            p = bar_at(session, m.symbol, prev, prev_close_t)
            last = bar_at(session, m.symbol, day, upto)
            funds.append(cf.FundIn(m.symbol, m.leverage, shares.get(m.symbol),
                                   p[0] if p else None, last[0] if last else None, last[1] if last else None))
        ip = official_close(session, g, prev) or (bar_at(session, g, prev, prev_close_t) or (None,))[0]
        il = bar_at(session, g, day, upto)
        res = cf.group_flow(g, funds, ip, il[0] if il else None, now)
        res["index_ts"] = il[1].isoformat() if il else None
        out_groups.append(res)

    rows = [
        {
            "grp": r["group"], "ts": now.replace(microsecond=0), "date": day,
            "index_prev": r["index_prev"], "index_last": r["index_last"], "index_ret": r["index_ret"],
            "rebalance_usd": r["rebalance_usd"], "futures": r["futures"], "per_1pct_usd": r["per_1pct_usd"],
            "aum_usd": r["aum_usd"], "direction": r["direction"], "strength": r["strength"],
            "n_funds": r["n_funds"], "n_index_fallback": r["n_index_fallback"], "n_missing": r["n_missing"],
            "funds": r["funds"], "source": "fmp_cvforge", "ingested_at": eastern_now(),
        }
        for r in out_groups
    ]
    upsert(session, LetfCloseFlow, rows, ["grp", "ts"])
    return {"day": day, "ts": now, "close": close_t, "groups": out_groups, "errors": errors}


def history(session: Session, day: date) -> dict[str, list[tuple[datetime, float]]]:
    q = select(LetfCloseFlow.grp, LetfCloseFlow.ts, LetfCloseFlow.rebalance_usd).where(
        LetfCloseFlow.date == day).order_by(LetfCloseFlow.ts)
    out: dict[str, list] = {}
    for g, ts, usd in session.execute(q).all():
        if usd is not None:
            out.setdefault(g, []).append((ts, float(usd)))
    return out


def write_outputs(session: Session, res: dict, folder: Path = COCKPIT_DATA) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    html = page.render(res, history(session, res["day"]))
    (folder / PAGE).write_text(html, encoding="utf-8", newline="\n")
    slim = {
        "day": res["day"].isoformat(), "ts": res["ts"].isoformat(timespec="seconds"),
        "errors": res["errors"],
        "groups": [{k: v for k, v in g.items() if k != "funds"} for g in res["groups"]],
    }
    (folder / JSON_OUT).write_text(json.dumps(slim, default=str), encoding="utf-8", newline="\n")
    return folder / PAGE


def maybe_telegram(res: dict, settings, folder: Path = COCKPIT_DATA) -> bool:  # noqa: ANN001
    close_dt = datetime.combine(res["day"], res["close"])
    if res["ts"] < close_dt - TG_FROM or res["ts"] > close_dt + TAIL:
        return False
    mark = folder / f"close_flow_tg_{res['day']:%Y%m%d}.done"
    if mark.exists():
        return False
    from trading_intel.clients.telegram import TelegramClient

    text = cf.telegram_line(res["groups"], res["ts"]) + "\nhttp://192.168.1.211:8899/close_flow.html"
    ok = TelegramClient(settings).send_message(text)
    if ok:
        mark.write_text(res["ts"].isoformat(), encoding="utf-8")
    return ok


def run(session: Session, cvf, settings, *, once: bool = False, now_fn=eastern_now, sleep=_time.sleep) -> int:  # noqa: ANN001
    now = now_fn()
    day = now.date()
    if not is_trading_session(day):
        log.info("letf_close_live.skip", reason="not a session", day=str(day))
        return 0
    close_dt = datetime.combine(day, cf.session_close(day))
    start, end = close_dt - LEAD, close_dt + TAIL
    if not once and now < start:
        log.info("letf_close_live.wait", until=start.isoformat())
        sleep((start - now).total_seconds())
    ok = fails = 0
    while True:
        now = now_fn()
        try:
            res = one_pass(session, cvf, now, settings)
            write_outputs(session, res)
            maybe_telegram(res, settings)
            ok += 1
            g = {r["group"]: (round(r["rebalance_usd"] / 1e6), r["direction"]) for r in res["groups"]}
            log.info("letf_close_live.pass", at=now.strftime("%H:%M"), usd_m=g, errors=res["errors"][:5])
        except Exception as exc:  # noqa: BLE001 - keep looping until the close
            session.rollback()
            fails += 1
            log.warning("letf_close_live.pass_failed", error=str(exc)[:200])
        if once or now_fn() >= end:
            break
        sleep(INTERVAL_S)
    try:
        from trading_intel.scheduler.track import report

        report(rows_attempted=ok + fails, rows_written=ok)
    except Exception:  # noqa: BLE001, S110 - bookkeeping only
        pass
    if ok == 0:
        raise RuntimeError(f"letf_close_live: every pass failed ({fails})")
    return ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--once", action="store_true", help="one pass now, no waiting")
    a = ap.parse_args(sys.argv[1:] if argv is None else argv)
    from trading_intel.clients.cvforge import CVForgeClient
    from trading_intel.config import get_settings
    from trading_intel.memory.db import make_session_factory

    settings = get_settings()
    cvf = CVForgeClient(settings)
    with make_session_factory(settings)() as session:
        run(session, cvf, settings, once=a.once)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
