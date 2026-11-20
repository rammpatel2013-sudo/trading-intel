"""etf_board - created 2026-11-19. Mithil's ToS "Alt ETF Selects" board on our data (spec
claude/etf-board-spec-20261119.md; maths in trading_intel/etf_board/calc.py).

Steps (default both):
  collect  vendors -> DB: daily bars for the 48 into equity_bars (FMP via CVForge, the equity pipeline),
           today's 1-minute bars into bars_1m (FMP historical-chart/1min via CVForge; skipped when already
           there), the 48 into symbol_map with tag alt_etf_selects + trades_24h.
  build    DB only: equity_bars + bars_1m + flow_und_daily (Convex und, 16:25 flow_bank) -> etf_board_daily,
           then scripts/cvxmap/data/etf_board.json + etf_board.html -> http://192.168.1.211:8899/etf_board.html
Run: python -m trading_intel.scheduler.jobs.etf_board [--day YYYY-MM-DD] [--steps collect,build] [--force]
DSM "TI etf board" Mon-Fri 16:45: run_job.sh etf_board   (after flow_bank 16:25). Descriptors only (rule 4).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import uuid
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_intel.clients.symbols import map_row
from trading_intel.config import Settings, get_settings
from trading_intel.equity.store import upsert
from trading_intel.etf_board import CORR_REF, ETF_LIST, TAG, TRADES_24H, calc
from trading_intel.memory.models_equity import EquityBar
from trading_intel.memory.models_etf import FLOAT_COLS, EtfBoardDaily
from trading_intel.memory.models_flow import Bars1m, FlowUndDaily
from trading_intel.memory.models_ref import SymbolMap
from trading_intel.timeutils import eastern_now, is_trading_session

log = structlog.get_logger()
ROOT = Path(__file__).resolve().parents[3]
COCKPIT_DATA = ROOT / "scripts" / "cvxmap" / "data"
PAGE_SRC = ROOT / "scripts" / "cvxmap" / "etf_board.html"
RTH = (time(9, 30), time(16, 0))
MIN_MINUTES = 300


# ── collect (vendors -> DB) ────────────────────────────────────────────
def collect(session: Session, settings: Settings, day: date) -> dict:
    from trading_intel.clients.cvforge import CVForgeClient
    from trading_intel.clients.equity_data import default_source
    from trading_intel.equity import pipeline
    from trading_intel.flow import bank

    out: dict = {}
    now = eastern_now().replace(microsecond=0)
    rows = []
    for s in ETF_LIST:
        r = map_row(s, "stock" if s == "BRK-B" else "etf")
        r.update(tags=TAG, trades_24h=s in TRADES_24H, ingested_at=now)
        rows.append(r)
    out["symbol_map"] = upsert(session, SymbolMap, rows, ["symbol"])
    out["daily_bars"] = pipeline.refresh_bars(
        session, default_source(settings), list(ETF_LIST), day
    )
    have = dict(
        session.execute(
            select(Bars1m.symbol, func.count())
            .where(
                Bars1m.ts >= datetime.combine(day, time(0)),
                Bars1m.ts < datetime.combine(day + timedelta(days=1), time(0)),
            )
            .group_by(Bars1m.symbol)
        ).all()
    )
    cv = CVForgeClient(settings)
    got, bad = 0, []
    for s in ETF_LIST:
        if have.get(s, 0) >= MIN_MINUTES:
            continue
        try:
            raw = cv.fmp(
                "historical-chart/1min",
                {"symbol": s, "from": day.isoformat(), "to": day.isoformat()},
            )
        except Exception as exc:
            bad.append(f"{s}: {str(exc)[:60]}")
            continue
        r1 = [r for r in bank.parse_fmp_1m(s, raw) if r["ts"].date() == day]
        got += bank.save_bars_1m(session, r1) if r1 else 0
    out["minute_rows"] = got
    out["minute_failed"] = bad
    return out


# ── build (DB only) ────────────────────────────────────────────────────
def _daily(session: Session, syms: list[str], day: date) -> dict[str, list[tuple]]:
    q = (
        select(
            EquityBar.symbol,
            EquityBar.date,
            EquityBar.open,
            EquityBar.high,
            EquityBar.low,
            EquityBar.close,
            EquityBar.volume,
        )
        .where(
            EquityBar.symbol.in_(syms),
            EquityBar.date >= day - timedelta(days=130),
            EquityBar.date <= day,
        )
        .order_by(EquityBar.date)
    )
    out: dict[str, list[tuple]] = defaultdict(list)
    for s, d, o, h, lo, c, v in session.execute(q).all():
        if c is not None:
            out[s].append(
                (
                    d,
                    o if o is not None else c,
                    h if h is not None else c,
                    lo if lo is not None else c,
                    c,
                    v or 0,
                )
            )
    return out


def _minutes(session: Session, syms: list[str], day: date) -> dict[str, list[tuple]]:
    q = (
        select(
            Bars1m.symbol,
            Bars1m.ts,
            Bars1m.open,
            Bars1m.high,
            Bars1m.low,
            Bars1m.close,
            Bars1m.volume,
        )
        .where(
            Bars1m.symbol.in_(syms),
            Bars1m.ts >= datetime.combine(day, RTH[0]),
            Bars1m.ts < datetime.combine(day, RTH[1]),
            Bars1m.source == "fmp",
        )
        .order_by(Bars1m.ts)
    )
    out: dict[str, list[tuple]] = defaultdict(list)
    for s, _ts, o, h, lo, c, v in session.execute(q).all():
        out[s].append((o, h, lo, c, v or 0))
    return out


def _und(session: Session, syms: list[str], day: date) -> tuple[dict, dict]:
    """(today's und row per symbol, {symbol: [(date, iv)] oldest first} for IV changes)."""
    dates = [
        d
        for (d,) in session.execute(
            select(FlowUndDaily.date)
            .where(FlowUndDaily.date <= day)
            .distinct()
            .order_by(FlowUndDaily.date.desc())
            .limit(6)
        ).all()
    ]
    now: dict = {}
    hist: dict[str, list] = defaultdict(list)
    if not dates:
        return now, hist
    for r in session.execute(
        select(FlowUndDaily)
        .where(FlowUndDaily.symbol.in_(syms), FlowUndDaily.date.in_(dates))
        .order_by(FlowUndDaily.date)
    ).scalars():
        hist[r.symbol].append((r.date, r.volatility))
        if r.date == dates[0] == day:
            now[r.symbol] = r
    return now, hist


def _net_hist(session: Session, syms: list[str], day: date) -> dict[str, list[float]]:
    q = (
        select(
            EtfBoardDaily.symbol, EtfBoardDaily.date, EtfBoardDaily.buy_usd, EtfBoardDaily.sell_usd
        )
        .where(
            EtfBoardDaily.symbol.in_(syms),
            EtfBoardDaily.date < day,
            EtfBoardDaily.date >= day - timedelta(days=20),
        )
        .order_by(EtfBoardDaily.date)
    )
    out: dict[str, list[float]] = defaultdict(list)
    for s, _d, b, se in session.execute(q).all():
        if b is not None and se is not None:
            out[s].append(b - se)
    return out


def board_row(
    sym: str,
    day: date,
    bars: list[tuple],
    minutes: list[tuple],
    ref: dict,
    und,
    iv_hist: list,
    net_hist: list[float],
) -> dict:
    """One board row from the DB inputs (pure; tested)."""
    bars = [b for b in bars if b[0] <= day]
    if minutes and (
        not bars or bars[-1][0] < day
    ):  # today's daily bar not stored yet -> from the minutes
        bars = bars + [
            (
                day,
                minutes[0][0],
                max(m[1] for m in minutes),
                min(m[2] for m in minutes),
                minutes[-1][3],
                sum(m[4] for m in minutes),
            )
        ]
    row: dict = {
        "date": day,
        "symbol": sym,
        "trades_24h": sym in TRADES_24H,
        "n_minutes": len(minutes),
    }
    if not bars:
        return row
    has_today = bars[-1][0] == day
    closes = [b[4] for b in bars]
    row.update(
        calc.daily_stats(closes, [b[2] for b in bars], [b[3] for b in bars], [b[5] for b in bars])
    )
    prev = closes[-2] if len(closes) >= 2 else None
    row["last"] = minutes[-1][3] if minutes else closes[-1]
    row["prev_close"] = prev
    if (
        not has_today
    ):  # no bar and no minutes for the day: the row describes the last stored session
        row["n_minutes"] = 0
    if has_today:
        o, h, lo, _c, v = bars[-1][1:]
        row.update(open=o, high=h, low=lo, volume=v)
        if o:
            row["chg_open"] = row["last"] / o - 1
    if minutes:
        b, s = calc.buy_sell(minutes, prev)
        row.update(buy_usd=b, sell_usd=s, bsr=calc.bsr(b, s))
        vol = sum(m[4] for m in minutes)
        row["vwap"] = (b + s) / vol if vol else None
        avg_usd = sum(x[4] * x[5] for x in bars[-21:-1]) / 20 if len(bars) >= 21 else None
        row["building"] = calc.building([*net_hist, b - s], avg_usd)
    cl = {b[0]: b[4] for b in bars}
    row["corr10"] = calc.corr_vs(ref, cl, 10)
    row["corr20"] = calc.corr_vs(ref, cl, 20)
    if und is not None:
        cv, pv = und.call_volume, und.put_volume
        row.update(
            call_vol=cv,
            put_vol=pv,
            pc_ratio=(
                und.put_call_ratio if und.put_call_ratio is not None else (pv / cv if cv else None)
            ),
            iv=und.volatility,
            iv_front=und.front_volatility,
            iv_back=und.back_volatility,
        )
        if und.front_volatility is not None and und.back_volatility is not None:
            row["iv_diff"] = und.front_volatility - und.back_volatility
        ivs = [x[1] for x in iv_hist if x[1] is not None]
        if und.volatility is not None and len(ivs) >= 2:
            row["iv_chg_1d"] = (ivs[-1] - ivs[-2]) * 100
        if und.volatility is not None and len(ivs) >= 6:
            row["iv_chg_5d"] = (ivs[-1] - ivs[-6]) * 100
    return row


def build(session: Session, day: date, write_page: bool = True) -> list[dict]:
    syms = list(dict.fromkeys([*ETF_LIST, CORR_REF]))
    daily = _daily(session, syms, day)
    mins = _minutes(session, syms, day)
    und, ivh = _und(session, syms, day)
    net = _net_hist(session, syms, day)
    ref = {b[0]: b[4] for b in daily.get(CORR_REF, [])}
    if mins.get(CORR_REF) and (not daily.get(CORR_REF) or daily[CORR_REF][-1][0] < day):
        ref[day] = mins[CORR_REF][-1][3]
    now = eastern_now().replace(microsecond=0)
    rows = []
    for s in ETF_LIST:
        r = board_row(
            s,
            day,
            daily.get(s, []),
            mins.get(s, []),
            ref,
            und.get(s),
            ivh.get(s, []),
            net.get(s, []),
        )
        r["computed_at"] = now
        rows.append(
            {
                k: r.get(k)
                for k in (
                    "date",
                    "symbol",
                    "mo3",
                    "n_minutes",
                    "trades_24h",
                    "computed_at",
                    *FLOAT_COLS,
                )
            }
        )
    upsert(session, EtfBoardDaily, rows, ["date", "symbol"])
    if write_page:
        write_outputs(rows, day, COCKPIT_DATA)
    return rows


def _jsonable(r: dict) -> dict:
    out = {}
    for k, v in r.items():
        if isinstance(v, (date, datetime)):
            out[k] = v.isoformat()
        elif isinstance(v, float):
            out[k] = round(v, 6)
        else:
            out[k] = v
    return out


def write_outputs(rows: list[dict], day: date, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    j = {
        "v": 1,
        "date": day.isoformat(),
        "tag": TAG,
        "rows": [_jsonable(r) for r in rows],
        "note": "Our definitions where ToS's are unknown (Strength, Building, Buying|Selling, Stretch, Mo1-3) - "
        "see trading_intel/etf_board/calc.py. Options columns from Convex und after the close.",
    }
    tmp = folder / "etf_board.json.tmp"
    tmp.write_text(json.dumps(j, separators=(",", ":")), encoding="utf-8")
    tmp.replace(folder / "etf_board.json")
    if PAGE_SRC.exists():
        shutil.copyfile(PAGE_SRC, folder / "etf_board.html")
    return folder / "etf_board.json"


def run(
    session: Session,
    settings: Settings | None = None,
    day: date | None = None,
    steps=("collect", "build"),
    force: bool = False,
) -> dict:
    settings = settings or get_settings()
    day = day or eastern_now().date()
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="etf_board")
    if not force and not is_trading_session(day):
        bound.info("etf_board.skip", reason="not_a_trading_session")
        return {"skipped": "not_a_trading_session"}
    out: dict = {}
    if "collect" in steps:
        out["collect"] = collect(session, settings, day)
    if "build" in steps:
        rows = build(session, day)
        out["rows"] = len(rows)
        out["with_minutes"] = sum(1 for r in rows if r.get("n_minutes"))
    bound.info("etf_board.done", **{k: v for k, v in out.items() if k != "collect"})
    try:
        from trading_intel.scheduler import track

        track.report(rows_attempted=len(ETF_LIST), rows_written=out.get("rows"))
    except Exception:  # noqa: S110 - bookkeeping only
        pass
    return out


def main(argv: list[str] | None = None) -> int:
    from trading_intel.memory.db import make_session_factory

    ap = argparse.ArgumentParser(prog="etf_board")
    ap.add_argument("--day")
    ap.add_argument("--steps", default="collect,build")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    s = get_settings()
    with make_session_factory(s)() as session:
        res = run(
            session,
            s,
            date.fromisoformat(a.day) if a.day else None,
            tuple(a.steps.split(",")),
            a.force,
        )
    print(json.dumps(res, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
