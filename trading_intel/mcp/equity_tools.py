"""MCP tools for the equity / swing side (2026-11-14, E1-E6). Read the NAS DB; ``check_book`` may
call FMP once per ETF to look through its holdings (rule 1: via ``clients.equity_data``).

* ``get_equity_gate``        - today's green / amber / red + each vote in plain words, recent history
* ``get_equity_candidates``  - leaders / coiled springs / breakouts (latest day)
* ``get_stock_factors``      - one stock: style exposures, sector, own move over 1 / 3 / 6 months
* ``get_factor_tides``       - what the market paid for over N sessions (style + sector returns)
* ``check_book``             - holdings -> factor exposures, sector weights, predicted volatility, flags

Wire in server.py:
    from trading_intel.mcp import equity_tools
    equity_tools.register(mcp, settings, session_factory)
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, timedelta
from typing import Any

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from trading_intel.equity import gate as gt
from trading_intel.equity import risk
from trading_intel.equity.factor_model import covariance
from trading_intel.equity.styles import STYLES
from trading_intel.memory.models_equity import (
    EquityCandidate,
    EquityFactorReturn,
    EquityGateDaily,
    EquityStockFactor,
)


def _iso(v: Any) -> Any:  # noqa: ANN401
    return v.isoformat() if isinstance(v, date) else v


def _row(obj: Any) -> dict:  # noqa: ANN401
    return {c.name: _iso(getattr(obj, c.name)) for c in obj.__table__.columns}


def equity_gate(session: Session, days: int = 20) -> dict[str, Any]:
    rows = list(
        session.execute(
            select(EquityGateDaily).order_by(EquityGateDaily.date.desc()).limit(max(1, days))
        ).scalars()
    )
    if not rows:
        return {"found": False, "hint": "equity backfill not run yet"}
    g = _row(rows[0])
    votes = {k: v for k, v in (g.get("votes") or {}).items() if not k.startswith("_")}
    return {
        "found": True,
        "date": g["date"],
        "color": g["color"],
        "score": g["score"],
        "headline": gt.headline({**g, "n_votes": len(votes), "trend": g.get("trend_note")}),
        "votes": [{"vote": k, "means": gt.PLAIN.get(k, ""), "value": v} for k, v in votes.items()],
        "numbers": {
            k: g[k]
            for k in (
                "median_drawdown",
                "pct_down_10",
                "pct_down_20",
                "repair_flow",
                "hidden_bear_gap",
                "pct_above_50",
                "pct_above_200",
                "nhnl_10d",
            )
        },
        "new_highs_lows_status": (g.get("votes") or {}).get("_nhnl_status"),
        "recent": [{"date": _iso(r.date), "color": r.color, "score": r.score} for r in rows],
        "rule": "v1 2026-11-14, thresholds untested; descriptor only",
    }


def equity_candidates(
    session: Session, list_name: str | None = None, limit: int = 15
) -> dict[str, Any]:
    day = session.execute(select(func.max(EquityCandidate.date))).scalar()
    if day is None:
        return {"found": False}
    q = select(EquityCandidate).where(EquityCandidate.date == day)
    if list_name:
        q = q.where(EquityCandidate.list_name == list_name)
    rows = [
        _row(r)
        for r in session.execute(
            q.order_by(EquityCandidate.list_name, EquityCandidate.rank)
        ).scalars()
    ]
    out: dict[str, list] = {}
    for r in rows:
        bucket = out.setdefault(r["list_name"], [])
        if len(bucket) < limit:
            bucket.append(r)
    return {
        "found": True,
        "date": _iso(day),
        "lists": out,
        "means": {
            "leader": "strongest own move (not explained by market/sector/style) with the trend intact",
            "coiled": "stuck in a <=20% box for 40-180 sessions above the 200-day line",
            "breakout": "closed above its box top on >=1.5x volume today",
        },
    }


def stock_factors(session: Session, symbol: str) -> dict[str, Any]:
    sym = symbol.strip().upper().replace(".", "-")
    rows = list(
        session.execute(
            select(EquityStockFactor)
            .where(EquityStockFactor.symbol == sym)
            .order_by(EquityStockFactor.date)
        ).scalars()
    )
    if not rows:
        return {"found": False, "symbol": sym}
    last = rows[-1]
    res = np.array([r.resid for r in rows if r.resid is not None])

    def own(n: int) -> float | None:
        return round(float(res[-n:].sum()) * 100, 2) if len(res) >= n * 0.7 else None

    return {
        "found": True,
        "symbol": sym,
        "date": _iso(last.date),
        "sector": last.sector,
        "exposures": {s: getattr(last, s) for s in STYLES},
        "own_move_pct": {"1m": own(21), "3m": own(63), "6m": own(126)},
        "means": "exposures are z-scores vs the S&P 1500 that day (0 = average, +1 = one st.dev above); "
        "own move = return not explained by market, sector and style tides",
    }


def factor_tides(session: Session, days: int = 20) -> dict[str, Any]:
    dates = [
        d
        for (d,) in session.execute(
            select(EquityFactorReturn.date)
            .distinct()
            .order_by(EquityFactorReturn.date.desc())
            .limit(max(1, days))
        )
    ]
    if not dates:
        return {"found": False}
    rows = session.execute(
        select(EquityFactorReturn).where(EquityFactorReturn.date.in_(dates))
    ).scalars()
    tot: dict[str, float] = {}
    kind: dict[str, str] = {}
    for r in rows:
        tot[r.factor] = tot.get(r.factor, 0.0) + (r.ret or 0.0)
        kind[r.factor] = r.kind or ""
    pct = {k: round(v * 100, 2) for k, v in tot.items()}
    return {
        "found": True,
        "from": _iso(min(dates)),
        "to": _iso(max(dates)),
        "sessions": len(dates),
        "market_pct": pct.get("market"),
        "styles_pct": {k: v for k, v in pct.items() if kind.get(k) == "style"},
        "sectors_vs_market_pct": dict(
            sorted(
                ((k.split(":", 1)[1], v) for k, v in pct.items() if kind.get(k) == "sector"),
                key=lambda kv: -kv[1],
            )
        ),
    }


def book(
    session: Session,
    holdings: Mapping[str, float],
    etf_lookup: Callable[[str], list[dict]] | None = None,
) -> dict[str, Any]:
    """``holdings`` = {symbol: dollars or weight}. ETFs are looked through when ``etf_lookup`` is given."""
    total = sum(abs(v) for v in holdings.values()) or 1.0
    day = session.execute(select(func.max(EquityStockFactor.date))).scalar()
    if day is None:
        return {"found": False}
    expo = {
        r.symbol: {**{s: getattr(r, s) for s in STYLES}, "sector": r.sector}
        for r in session.execute(
            select(EquityStockFactor).where(EquityStockFactor.date == day)
        ).scalars()
    }
    weights: dict[str, float] = {}
    unseen: list[str] = []
    for sym, v in holdings.items():
        s = sym.strip().upper().replace(".", "-")
        w = v / total
        if s in expo:
            weights[s] = weights.get(s, 0.0) + w
            continue
        hold = etf_lookup(s) if etf_lookup else []
        hw = {
            str(h.get("asset", "")).upper().replace(".", "-"): float(h.get("weightPercentage") or 0)
            / 100
            for h in hold
            if h.get("asset")
        }
        if hw:
            for k, x in hw.items():
                if k in expo:
                    weights[k] = weights.get(k, 0.0) + w * x
        else:
            unseen.append(s)
    fac_rows = [
        {"date": r.date, "factor": r.factor, "ret": r.ret}
        for r in session.execute(
            select(EquityFactorReturn).where(EquityFactorReturn.date > day - timedelta(days=365))
        ).scalars()
    ]
    names = sorted({r["factor"] for r in fac_rows})
    cov = covariance(fac_rows, names) if fac_rows else np.zeros((0, 0))
    resid_var: dict[str, float] = {}
    for sym in weights:
        res = [
            x
            for x in session.execute(
                select(EquityStockFactor.resid)
                .where(EquityStockFactor.symbol == sym)
                .order_by(EquityStockFactor.date.desc())
                .limit(63)
            ).scalars()
            if x is not None
        ]
        resid_var[sym] = float(np.var(res)) if len(res) > 10 else 0.0
    out = risk.book_risk(weights, expo, cov, names, resid_var)
    return {
        "found": True,
        "date": _iso(day),
        **out,
        "not_covered": unseen,
        "means": "style exposure 0 = like the average S&P 1500 stock; predicted vol = yearly, from the "
        "factor model; risk share = where the swings would come from",
    }


def register(
    mcp: Any, settings: Any, session_factory: sessionmaker[Session] | None
) -> None:
    if session_factory is None:
        return

    @mcp.tool()
    def get_equity_gate(days: int = 20) -> dict[str, Any]:
        """Is it safe to buy stocks? Green / amber / red with each vote in plain words + recent days."""
        with session_factory() as s:
            return equity_gate(s, days)

    @mcp.tool()
    def get_equity_candidates(list_name: str = "", limit: int = 15) -> dict[str, Any]:
        """Swing candidates: leaders (own-move strength), coiled springs (box), breakouts. list_name optional."""
        with session_factory() as s:
            return equity_candidates(s, list_name or None, limit)

    @mcp.tool()
    def get_stock_factors(symbol: str) -> dict[str, Any]:
        """One stock's factor exposures (momentum, size, value, beta, volatility, quality, growth) and own move."""
        with session_factory() as s:
            return stock_factors(s, symbol)

    @mcp.tool()
    def get_factor_tides(days: int = 20) -> dict[str, Any]:
        """What the market paid for over N sessions: market, style and sector-vs-market returns."""
        with session_factory() as s:
            return factor_tides(s, days)

    @mcp.tool()
    def check_book(holdings: dict[str, float]) -> dict[str, Any]:
        """Holdings {symbol: dollars} -> style exposures, sectors, predicted volatility, hidden-bet flags.

        ETFs (SPY, QQQ, sector ETFs ...) are looked through to their holdings."""
        from trading_intel.clients.equity_data import default_source

        src = default_source(settings)
        with session_factory() as s:
            return book(s, holdings, etf_lookup=src.etf_holdings)
