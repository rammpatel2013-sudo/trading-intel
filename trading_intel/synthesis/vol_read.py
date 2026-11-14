"""Vol Read loader: banked rows -> Lakha checks + fear release (2026-11-13 overnight).

SPX v1 (spec claude/lakha-read-plan.md). Reads iv_tenor_snapshots (30d ATM + 25d RR),
quotes_daily SPX, vix_data and squeezemetrics_daily. Percentiles over 252 sessions with the
trading-session filter (iv_tenor has weekend carry rows). Descriptive only (rule 4).
"""
from __future__ import annotations

import math
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.market import lakha_read as lr
from trading_intel.memory.models import IvTenorSnapshot, QuoteDaily, SqueezeMetricsDaily, VixData
from trading_intel.timeutils import is_trading_session

log = structlog.get_logger(__name__)


def _rv(closes: list[float], n: int = 10) -> float | None:
    if len(closes) < n + 1:
        return None
    r = [math.log(closes[i] / closes[i - 1]) for i in range(len(closes) - n, len(closes))]
    m = sum(r) / n
    return 100 * math.sqrt(sum((x - m) ** 2 for x in r) / (n - 1) * 252)


def _legs(c: list[float], iv: list[float]) -> tuple[float | None, float | None]:
    """IV pts per 1% drop on the previous down-leg vs the latest one (last 40 sessions)."""
    c, iv = c[-40:], iv[-40:]
    if len(c) < 15:
        return None, None
    lows = [i for i in range(2, len(c) - 1) if c[i] <= min(c[i - 2:i + 2])]
    if len(lows) < 2:
        return None, None
    l2, l1 = lows[-1], lows[-2]
    hi1 = max(range(0, l1 + 1), key=lambda i: c[i])
    hi2 = max(range(l1, l2 + 1), key=lambda i: c[i])

    def per(a, b):
        drop = 100 * (c[a] / c[b] - 1)
        return (iv[b] - iv[a]) / drop if drop > 0.5 else None
    return per(hi1, l1), per(hi2, l2)


def build(session: Session) -> dict[str, Any]:
    ivr = [r for r in session.execute(
        select(IvTenorSnapshot).where(IvTenorSnapshot.symbol == "SPX", IvTenorSnapshot.tenor_dte == 30)
        .order_by(IvTenorSnapshot.ts.desc()).limit(400)).scalars().all() if is_trading_session(r.ts)]
    ivr = list(reversed(ivr))[-252:]
    spx = list(reversed(session.execute(select(QuoteDaily).where(QuoteDaily.symbol == "SPX")
                                        .order_by(QuoteDaily.date.desc()).limit(260)).scalars().all()))
    vix = list(reversed(session.execute(select(VixData).where(VixData.vix.isnot(None))
                                        .order_by(VixData.date.desc()).limit(260)).scalars().all()))
    if len(ivr) < 3 or len(spx) < 12 or len(vix) < 2:
        return {"found": False, "reason": "not enough history"}
    by_d = {q.date: float(q.close) for q in spx}
    atm = [100 * (r.iv_atm or 0) for r in ivr]
    rr = [100 * ((r.iv_put_25d or 0) - (r.iv_call_25d or 0)) for r in ivr]
    c = [float(q.close) for q in spx]
    sp_chg = [100 * (by_d[ivr[i].ts] / by_d[ivr[i - 1].ts] - 1) for i in range(1, len(ivr))
              if ivr[i].ts in by_d and ivr[i - 1].ts in by_d]
    iv_chg = [atm[i] - atm[i - 1] for i in range(1, len(ivr)) if ivr[i].ts in by_d and ivr[i - 1].ts in by_d]
    v = vix[-1]
    ma10 = sum(c[-10:]) / 10
    gap = 100 * (float(spx[-1].open) / c[-2] - 1) if c[-2] else 0.0
    ivc = [100 * (r.iv_atm or 0) for r in ivr if r.ts in by_d]
    cc = [by_d[r.ts] for r in ivr if r.ts in by_d]
    l1, l2 = _legs(cc, ivc)
    checks = [
        lr.spot_vs_vol(sp_chg or [0.0], iv_chg or [0.0]),
        lr.base_signal(l1, l2),
        lr.skew_gate(lr.pct_rank(rr, rr[-1]) if len(rr) >= 20 else None),
        lr.iv_percentile(lr.pct_rank(atm, atm[-1]) if len(atm) >= 20 else None),
        lr.carry(atm[-1], _rv(c) or atm[-1]),
        lr.vix_gap(float(v.vix), atm[-1]),
        lr.term(float(v.vix9d or v.vix), float(v.vix), float(v.vix3m or v.vix)),
        lr.long_gamma_proxy(c[-1], ma10, gap),
    ]
    info = [
        lr.divergence(sp_chg[-1] if sp_chg else 0.0, iv_chg[-1] if iv_chg else 0.0,
                      rr[-1] - rr[-2], c[-1] >= max(c[-60:])),
        lr.breakeven(atm[-1], sp_chg[-1] if sp_chg else 0.0, iv_chg[-1] if iv_chg else 0.0),
    ]
    ratio = [float(x.vix9d) / float(x.vix) for x in vix[-30:] if x.vix9d and x.vix]
    gex = [float(g.gex) for g in session.execute(select(SqueezeMetricsDaily).order_by(
        SqueezeMetricsDaily.date.desc()).limit(252)).scalars().all() if g.gex is not None]
    fear_days = [x.date for x in vix[-30:] if x.vix9d and x.vix and x.vix9d > x.vix]
    gp = None
    if gex and fear_days:
        gmap = {g.date: float(g.gex) for g in session.execute(select(SqueezeMetricsDaily).where(
            SqueezeMetricsDaily.date.in_(fear_days))).scalars().all() if g.gex is not None}
        if gmap:
            gp = lr.pct_rank(gex, min(gmap.values()))
    off_hi = 100 * (1 - c[-1] / max(c[-60:]))
    fr = lr.fear_release(ratio, gex_pct_in_fear=gp, spx_off_high_pct=off_hi)
    return {"found": True, "as_of": ivr[-1].ts.isoformat(), "checks": checks, "info": info,
            "verdict": lr.verdict(checks), "fear_release": fr, "n_sessions": len(ivr)}


def brief_line(vr: dict[str, Any]) -> str:
    if not vr.get("found"):
        return ""
    v = vr["verdict"]
    return f"Vol read: {v['score']:+d}/{v['of']} {v['band']}. {v['text']} {vr['fear_release']['line']}"
