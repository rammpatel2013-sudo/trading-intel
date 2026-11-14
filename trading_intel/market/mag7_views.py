"""Mag7 views for the swing side (2026-11-13 overnight; backlog #91 #92 #93, Mithil "keep now").

- weekly_moves: what options priced for each week (ATM IV of the expiry ~5-7 days out ->
  implied move = 0.8 x IV x sqrt(T) x spot, the straddle rule of thumb) vs the move that happened.
- gamma_grid: dealer gamma (naive sign: calls +, puts -) by strike x expiry from the latest EOD book.
- iv_history: ~30-day ATM IV per day with earnings dates, plus call / put premium from the tape roll-up.
All from banked rows (oi_chain_eod, quotes_daily, earnings_events, tas_daily_flow). Descriptor only (rule 4).
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_intel.memory.models import EarningsEvent, OiChainEod, QuoteDaily, TasDailyFlow

MAG7 = ("AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA")


def _closes(session: Session, sym: str, days: int = 130) -> dict[date, float]:
    rows = session.execute(select(QuoteDaily.date, QuoteDaily.close).where(QuoteDaily.symbol == sym)
                           .order_by(QuoteDaily.date.desc()).limit(days)).all()
    return {d: float(c) for d, c in rows}


def _atm_iv(session: Session, sym: str, ts, spot: float, dte_target: int) -> tuple[float | None, int | None]:
    rows = session.execute(select(OiChainEod.dte, OiChainEod.strike, OiChainEod.iv).where(
        OiChainEod.symbol == sym, OiChainEod.ts == ts, OiChainEod.iv.isnot(None), OiChainEod.dte.isnot(None))).all()
    if not rows:
        return None, None
    dtes = sorted({r.dte for r in rows if r.dte >= 1})
    if not dtes:
        return None, None
    dte = min(dtes, key=lambda d: abs(d - dte_target))
    near = sorted((r for r in rows if r.dte == dte), key=lambda r: abs(r.strike - spot))[:4]
    ivs = [r.iv for r in near if r.iv and 0 < r.iv < 5]
    return (sum(ivs) / len(ivs) if ivs else None), dte


def _books(session: Session, sym: str, n: int) -> list:
    return [r[0] for r in session.execute(select(func.max(OiChainEod.ts)).where(OiChainEod.symbol == sym)
                                          .group_by(func.date(OiChainEod.ts))
                                          .order_by(func.max(OiChainEod.ts).desc()).limit(n)).all()]


def weekly_moves(session: Session, sym: str, weeks: int = 12) -> list[dict[str, Any]]:
    closes = _closes(session, sym, 130)
    if not closes:
        return []
    out = []
    for ts in sorted(_books(session, sym, 80)):
        d = ts.date()
        if d.weekday() != 4 or d not in closes:  # Friday books -> next week
            continue
        iv, dte = _atm_iv(session, sym, ts, closes[d], 7)
        nxt = [x for x in sorted(closes) if d < x <= d + timedelta(days=7)]
        if iv is None or not nxt:
            continue
        implied = 0.8 * iv * math.sqrt(max(dte, 1) / 365) * 100
        realized = abs(closes[nxt[-1]] / closes[d] - 1) * 100
        out.append({"week_of": d.isoformat(), "implied_pct": implied, "realized_pct": realized,
                    "ratio": realized / implied if implied else None})
    return out[-weeks:]


def gamma_grid(session: Session, sym: str, *, band: float = 0.10, n_exp: int = 8) -> dict[str, Any]:
    ts = session.execute(select(func.max(OiChainEod.ts)).where(OiChainEod.symbol == sym)).scalar()
    closes = _closes(session, sym, 5)
    if ts is None or not closes:
        return {"found": False}
    spot = closes[max(closes)]
    rows = session.execute(select(OiChainEod.expiry, OiChainEod.strike, OiChainEod.cp, OiChainEod.gxoi).where(
        OiChainEod.symbol == sym, OiChainEod.ts == ts, OiChainEod.gxoi.isnot(None),
        OiChainEod.strike.between(spot * (1 - band), spot * (1 + band)))).all()
    exps = sorted({r.expiry for r in rows})[:n_exp]
    cell: dict[tuple, float] = defaultdict(float)
    for r in rows:
        if r.expiry in exps:
            cell[(r.expiry, r.strike)] += abs(r.gxoi) * (1 if r.cp == "C" else -1)
    strikes = sorted({k for _, k in cell})
    return {"found": bool(cell), "book": ts.isoformat(), "spot": spot, "expiries": [e.isoformat() for e in exps],
            "strikes": strikes, "grid": [[cell.get((e, k), 0.0) for e in exps] for k in strikes]}


def iv_history(session: Session, sym: str, days: int = 60) -> dict[str, Any]:
    closes = _closes(session, sym, days + 10)
    pts = []
    for ts in sorted(_books(session, sym, days)):
        d = ts.date()
        if d in closes:
            iv, _ = _atm_iv(session, sym, ts, closes[d], 30)
            if iv is not None:
                pts.append({"date": d.isoformat(), "iv": iv * 100})
    if not pts:
        return {"found": False}
    d0 = date.fromisoformat(pts[0]["date"])
    earn = [e.isoformat() for (e,) in session.execute(select(EarningsEvent.date).where(
        EarningsEvent.symbol == sym, EarningsEvent.date >= d0)).all()]
    flow = {r.trade_date.isoformat(): (r.call_notional or 0.0, r.put_notional or 0.0) for r in session.execute(
        select(TasDailyFlow).where(TasDailyFlow.root == sym, TasDailyFlow.trade_date >= d0)).scalars()}
    return {"found": True, "points": pts, "earnings": sorted(set(earn)), "flow": flow}
