"""Equity pipeline steps shared by the daily / weekly jobs and the one-off backfill.

Every step is idempotent (upserts keyed on natural keys, rule 5) and logs what it did. Vendor
calls go only through ``clients/`` (rule 1): ``EquityDataSource`` (FMP on the CVForge key),
``thetradingtools.fetch_nhnl`` and ``CVForgeClient.chain``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from datetime import date, timedelta
from typing import Any

import numpy as np
import structlog
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from trading_intel.equity import candidates as cand
from trading_intel.equity import damage, gate, nhnl, parse, store, universe
from trading_intel.equity.factor_model import factor_rows, run_model, stock_rows
from trading_intel.equity.panel import Panel, load_panel
from trading_intel.equity.styles import compute_styles
from trading_intel.memory.models_equity import (
    EquityBar,
    EquityCallIv,
    EquityCandidate,
    EquityFactorReturn,
    EquityFundamental,
    EquityGateDaily,
    EquityProfile,
    EquityStockFactor,
    ExternalNhNl,
)

log = structlog.get_logger(__name__)
HISTORY_START = date(2023, 1, 1)
DAILY_LOOKBACK_DAYS = 840  # calendar days loaded for the daily model (~580 sessions)


# ── E1: universe, profiles, fundamentals, bars ──────────────────────────
def refresh_universe(
    session: Session, src: Any, today: date
) -> list[universe.Interval]:
    ivs = universe.sp500_intervals(src.sp500_current(), src.sp500_changes(), since=HISTORY_START)
    for code, etf in (("SP400", "IJH"), ("SP600", "IJR")):
        try:
            ivs += universe.etf_intervals(src.etf_holdings(etf), code, etf)
        except Exception as exc:
            log.warning("equity.universe.etf_failed", etf=etf, error=str(exc))
    n = store.replace_members(session, ivs)
    log.info("equity.universe.done", intervals=n, symbols=len({iv.symbol for iv in ivs}))
    return ivs


def target_symbols(ivs: Sequence[universe.Interval], since: date = HISTORY_START) -> list[str]:
    return sorted(set(universe.all_symbols(ivs, since=since)) | set(universe.BENCHMARKS))


def refresh_profiles(
    session: Session,
    src: Any,
    symbols: Iterable[str],
    today: date,
    *,
    max_age_days: int = 30,
) -> int:
    have = store.load_profiles(session)
    need = [
        s
        for s in symbols
        if s not in have or not have[s].updated or (today - have[s].updated).days > max_age_days
    ]
    got = src.fetch_many(need, src.profile, label="profiles")
    rows = [parse.profile_row(s, raw, today) for s, raw in got.items() if raw]
    return store.upsert(session, EquityProfile, rows, ["symbol"])


def refresh_fundamentals(
    session: Session, src: Any, symbols: Iterable[str], *, limit: int = 12
) -> int:
    syms = [s for s in symbols if s not in universe.BENCHMARKS]
    n = ok = 0
    for k in range(0, len(syms), 200):
        got = src.fetch_many(syms[k : k + 200], lambda s: src.statements(s, limit=limit),
                             label="fundamentals")
        for s, stmts in got.items():
            n += store.upsert(session, EquityFundamental, parse.fundamental_rows(s, stmts),
                              ["symbol", "period_end"], commit=False)
        ok += len(got)
        session.commit()
    log.info("equity.fundamentals.done", symbols=ok, of=len(syms), rows=n)
    return n


def refresh_bars(
    session: Session,
    src: Any,
    symbols: Iterable[str],
    today: date,
    *,
    overlap_days: int = 7,
    batch: int = 50,
) -> int:
    last = store.last_bar_dates(session)
    syms = list(symbols)
    starts = {
        s: (last[s] - timedelta(days=overlap_days)) if s in last else HISTORY_START for s in syms
    }
    funds = store.load_fundamentals(session, syms)
    n = ok = 0
    # 50 names per batch: a 4-year backfill of 1,700 names as raw JSON at once would not fit the NAS RAM
    for k in range(0, len(syms), batch):
        part = syms[k : k + batch]
        got = src.fetch_many(part, lambda s: src.bars(s, starts[s].isoformat()), label="bars")
        for s, raw in got.items():
            rows = store.attach_market_cap(parse.bar_rows(s, raw), funds.get(s, []))
            n += store.upsert(session, EquityBar, rows, ["symbol", "date"], commit=False, chunk=2000)
        ok += len(got)
        session.commit()
    log.info("equity.bars.done", symbols=ok, of=len(syms), rows=n)
    return n


def fill_market_caps(session: Session, symbols: Iterable[str] | None = None) -> int:
    """Recompute market_cap on stored bars after fundamentals arrive (backfill order fix)."""
    funds = store.load_fundamentals(session, symbols)
    n = 0
    for s, fl in funds.items():
        bars = [
            {
                "symbol": b[0],
                "date": b[1],
                "open": b[2],
                "high": b[3],
                "low": b[4],
                "close": b[5],
                "volume": b[6],
                "market_cap": b[7],
            }
            for b in store.load_bars(session, symbols=[s])
        ]
        if bars:
            n += store.upsert(
                session,
                EquityBar,
                store.attach_market_cap(bars, fl),
                ["symbol", "date"],
                commit=False,
                chunk=2000,
            )
    session.commit()
    return n


# ── E2: new highs / new lows ─────────────────────────────────────────────
def refresh_nhnl(
    session: Session,
    today: date,
    *,
    fetch: Callable[[], list] | None = None,
    since: date | None = None,
) -> dict:
    from trading_intel.clients.thetradingtools import fetch_nhnl

    try:
        raw = (fetch or fetch_nhnl)()
    except Exception as exc:
        log.warning("equity.nhnl.fetch_failed", error=str(exc))
        return {"status": "fetch_failed"}
    rows = nhnl.parse_rows(raw)  # suspect flags need the full history for their 20-day median
    if since:
        rows = [r for r in rows if r["date"] >= since]
    store.upsert(session, ExternalNhNl, rows, ["date"])
    last = rows[-1]["date"] if rows else None
    log.info("equity.nhnl.done", rows=len(rows), last=str(last))
    return {"status": "ok", "rows": len(rows), "last": last}


# ── E2-E4: the model run ─────────────────────────────────────────────────
def load(session: Session, today: date, *, start: date | None = None) -> Panel:
    ivs = store.load_members(session)
    start = start or (today - timedelta(days=DAILY_LOOKBACK_DAYS))
    funds = store.load_fundamentals(session)
    return load_panel(
        session, start, ivs, store.load_profiles(session), funds, extra_symbols=universe.BENCHMARKS
    )


def _breadth_inputs(session: Session, day: date) -> dict:
    """Stockbee quarter ratio + beta-reset label from the existing tables (best-effort)."""
    out: dict[str, Any] = {}
    try:
        from trading_intel.memory.models import BetaResetDaily, BreadthSnapshot

        b = (
            session.execute(
                select(BreadthSnapshot)
                .where(
                    BreadthSnapshot.source == "fmp_sp500",
                    BreadthSnapshot.ts <= day,
                    BreadthSnapshot.ts >= day - timedelta(days=4),
                )
                .order_by(BreadthSnapshot.ts.desc())
            )
            .scalars()
            .first()
        )
        if b and b.up25q is not None and b.down25q:
            out["q25_ratio"] = b.up25q / b.down25q
        br = (
            session.execute(
                select(BetaResetDaily)
                .where(BetaResetDaily.date <= day, BetaResetDaily.date >= day - timedelta(days=4))
                .order_by(BetaResetDaily.date.desc())
            )
            .scalars()
            .first()
        )
        if br and br.label:
            out["beta_reset_label"] = br.label
    except Exception as exc:
        log.warning("equity.gate.breadth_inputs_failed", error=str(exc))
    return out


def gate_rows(session: Session, p: Panel, *, last: int = 1) -> list[dict]:
    st = damage.damage_stats(p)
    a50, a200 = damage.pct_above_ma(p, 50), damage.pct_above_ma(p, 200)
    gap = damage.hidden_bear_gap(p)
    nh_rows = [
        {c.name: getattr(r, c.name) for c in ExternalNhNl.__table__.columns}
        for r in session.execute(select(ExternalNhNl).order_by(ExternalNhNl.date)).scalars()
    ]
    spy = p.col("SPY")
    T = len(p.dates)
    out: list[dict] = []
    scores: dict[int, int] = {}
    first = max(0, T - last - 5)
    for t in range(first, T):
        d = p.dates[t]
        nh10, nh_status = nhnl.ten_day(nh_rows, d) if nh_rows else (None, "not_collected")
        inp: dict[str, Any] = {
            "median_drawdown": _nz(st["median_drawdown"][t]),
            "pct_down_10": _nz(st["pct_down_10"][t]),
            "pct_down_20": _nz(st["pct_down_20"][t]),
            "repair_flow": _nz(st["repair_flow"][t]),
            "pct_above_50": _nz(a50[t]),
            "pct_above_200": _nz(a200[t]),
            "nhnl_10d": nh10,
            "hidden_bear_gap": _nz(gap[t]),
            **_breadth_inputs(session, d),
        }
        if spy is not None and t >= 199:
            c = p.close[:, spy]
            inp["spy_above_50"] = bool(c[t] > np.nanmean(c[t - 49 : t + 1]))
            inp["spy_above_200"] = bool(c[t] > np.nanmean(c[t - 199 : t + 1]))
        g = gate.decide(inp, score_5d_ago=scores.get(t - 5))
        scores[t] = g["score"]
        if t >= T - last:
            out.append(
                {
                    "date": d,
                    "color": g["color"],
                    "score": g["score"],
                    "trend_note": g["trend"],
                    "median_drawdown": inp["median_drawdown"],
                    "pct_down_10": inp["pct_down_10"],
                    "pct_down_20": inp["pct_down_20"],
                    "repair_flow": inp["repair_flow"],
                    "hidden_bear_gap": inp["hidden_bear_gap"],
                    "pct_above_50": inp["pct_above_50"],
                    "pct_above_200": inp["pct_above_200"],
                    "nhnl_10d": nh10,
                    "n_stocks": int(st["n"][t]),
                    "votes": {
                        **g["votes"],
                        "_nhnl_status": nh_status,
                        "_inputs": {
                            k: v for k, v in inp.items() if isinstance(v, (int, float, str, bool))
                        },
                    },
                }
            )
    return out


def _nz(x: float) -> float | None:
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), 3)


def compute(
    session: Session,
    today: date,
    *,
    write_days: int = 5,
    gate_days: int = 1,
    factor_days: int | None = 10,
    start: date | None = None,
    prune_days: int = 400,
) -> dict:
    """Panel -> styles -> factor model -> gate -> candidates; writes the newest rows."""
    p = load(session, today, start=start)
    if not p.dates:
        return {"skipped": "no_bars"}
    sty = compute_styles(p)
    run = run_model(p, styles=sty)
    fr = factor_rows(run, last=factor_days)
    store.upsert(session, EquityFactorReturn, fr, ["date", "factor"], commit=False)
    sr = stock_rows(run, p.sector, last=write_days)
    store.upsert(session, EquityStockFactor, sr, ["date", "symbol"], commit=False, chunk=2000)
    if prune_days:
        session.execute(
            delete(EquityStockFactor).where(
                EquityStockFactor.date < today - timedelta(days=prune_days)
            )
        )
    gr = gate_rows(session, p, last=gate_days)
    store.upsert(session, EquityGateDaily, gr, ["date"], commit=False)
    cr = cand.build(p, run)
    if cr:
        session.execute(delete(EquityCandidate).where(EquityCandidate.date == cr[0]["date"]))
        store.upsert(session, EquityCandidate, cr, ["date", "symbol", "list_name"], commit=False)
    session.commit()
    fit = run.fits[-1]
    log.info(
        "equity.compute.done",
        day=str(p.dates[-1]),
        n=len(p.symbols),
        factor_rows=len(fr),
        stock_rows=len(sr),
        gate=gr[-1]["color"] if gr else None,
        candidates=len(cr),
        r2=fit.r2 if fit else None,
    )
    return {
        "day": p.dates[-1],
        "panel": p,
        "run": run,
        "gate": gr[-1] if gr else None,
        "candidates": cr,
    }


# ── E4-E5: option checks on the shortlist ───────────────────────────────
def option_checks(
    session: Session, cv: Any, symbols: Sequence[str], today: date
) -> dict[str, dict]:
    """Bank 90-180 DTE call IV for each name; return chains' spread cards + call-bid reads."""
    from trading_intel.equity import trade_card as tc

    out: dict[str, dict] = {}
    iv_rows = []
    for s in symbols:
        try:
            ch = cv.chain(s)
        except Exception as exc:
            log.warning("equity.chain_failed", symbol=s, error=str(exc))
            continue
        row = tc.call_iv_row(ch, s, today)
        if row:
            iv_rows.append(row)
        out[s] = {"spread": tc.debit_spread(ch, today), "iv": row}
    store.upsert(session, EquityCallIv, iv_rows, ["date", "symbol"])
    for s in out:
        hist = [
            {"iv_call_25d": r.iv_call_25d, "spot": r.spot}
            for r in session.execute(
                select(EquityCallIv).where(EquityCallIv.symbol == s).order_by(EquityCallIv.date)
            ).scalars()
        ]
        out[s]["call_bid"] = tc.call_bid(hist)
    return out
