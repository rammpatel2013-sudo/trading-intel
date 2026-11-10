"""Event-day tools (2026-11-10, after the VolSignals CPI thread): hedge response, close odds by price bucket,
and how SPX moved on past event days split by the dealer-gamma sign at the prior close.

    get_hedge_response  - ES contracts dealers would trade for each SPX move off the book's spot, full book
                          and without the front expiry (the "shot clock": what is left after today's close).
    get_close_odds      - option-implied odds of where the front expiry settles, by price bucket
                          (Breeden-Litzenberger on the smile: P(S_T > K) = -dC/dK with r = 0).
    get_event_day_stats - past event days (CPI by default, FRED release 10) with the SPX day range and
                          close-to-close move, split by the naive dealer-gamma sign at the prior close.

All naive dealer book (calls +, puts -) like get_profile; positioning reads, never signals (rule 4).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.greeks.black_scholes import bs_call_price
from trading_intel.mcp import profile_tool as pt
from trading_intel.memory.models import OiChainDaily, OiChainEod, QuoteDaily
from trading_intel.timeutils import live_expiry_floor, same_day_years

ES_MULT = 50.0
DEFAULT_MOVES = (-2.0, -1.0, -0.5, 0.5, 1.0, 2.0)
FRED_CPI_RELEASE = 10
_trapz = getattr(np, "trapezoid", None) or np.trapz  # numpy < 2 has only trapz


# --------------------------------------------------------------------- hedge response
def hedge_from_curve(spot_ref: list[float], gamma_bn: list[float], s0: float, pct: float) -> float:
    """ES contracts dealers trade for a move of ``pct`` % off ``s0``, from a gamma-vs-spot curve in
    $bn of delta per 1% move. Delta change = integral of G(s) / (0.01 s) ds; dealers hedge the opposite
    way, so the trade = -change. Negative = sell."""
    s1 = s0 * (1.0 + pct / 100.0)
    xs = np.linspace(s0, s1, 400)
    g = np.interp(xs, np.asarray(spot_ref, float), np.asarray(gamma_bn, float))
    d_delta = float(_trapz(g / (0.01 * xs), xs)) * 1e9
    return -d_delta / (ES_MULT * s1)


def get_hedge_response(
    session: Session, symbol: str = "SPX", *, as_of: date | None = None,
    moves: tuple[float, ...] = DEFAULT_MOVES,
) -> dict[str, Any]:
    """ES contracts (thousands) market makers would trade for each move, full book and without the
    front expiry. Naive sign: read the SHAPE (which way, where it turns) more than the size."""
    span = max(0.03, max(abs(m) for m in moves) / 100.0 + 0.005)
    prof = pt.get_profile(session, symbol, span=span, n_points=161, as_of=as_of, greeks=("gamma",))
    if not prof.get("found"):
        return {"symbol": symbol.upper(), "found": False}
    gp = prof["profiles"]["gamma"]
    ref, full = prof["profiles"]["spot_ref"], gp["all"]
    by_exp = gp.get("by_expiry") or {}
    front = min(by_exp) if by_exp else None
    ex_front = [a - b for a, b in zip(full, by_exp[front], strict=True)] if front else full
    s0 = float(prof["spot"])
    rows = []
    for m in moves:
        rows.append({
            "move_pct": m, "spx": round(s0 * (1 + m / 100.0), 1),
            "es_k": round(hedge_from_curve(ref, full, s0, m) / 1000.0, 1),
            "es_k_ex_front": round(hedge_from_curve(ref, ex_front, s0, m) / 1000.0, 1),
        })
    return {"symbol": prof["symbol"], "as_of": prof["as_of"], "spot": s0, "found": True,
            "front_expiry": front, "rows": rows,
            "units": "thousand ES contracts; negative = dealers sell, positive = buy",
            "note": "naive book (dealers long calls, short puts); sticky strike; size overstates vs signed books"}


# --------------------------------------------------------------------- close odds
def odds_from_smile(strikes: np.ndarray, ivs: np.ndarray, spot: float, t: float,
                    edges: list[float]) -> list[float]:
    """P(S_T in each bucket between consecutive ``edges``) from a smile (strike -> IV), r = q = 0.
    Survival P(S_T > K) = -dC/dK on a dense strike grid; buckets below the first / above the last edge
    are included as the first / last entries (len = len(edges) + 1)."""
    o = np.argsort(strikes)
    k, v = np.asarray(strikes, float)[o], np.asarray(ivs, float)[o]
    grid = np.linspace(k[0], k[-1], 4001)
    sig = np.interp(grid, k, v)
    c = bs_call_price(spot, grid, sig, np.full_like(grid, t))
    surv = np.clip(-np.gradient(c, grid), 0.0, 1.0)
    surv = np.minimum.accumulate(surv)            # survival can only fall with strike
    sv = [float(np.interp(e, grid, surv, left=1.0, right=0.0)) for e in edges]
    out = [1.0 - sv[0]] + [sv[i] - sv[i + 1] for i in range(len(sv) - 1)] + [sv[-1]]
    return [max(0.0, x) for x in out]


def get_close_odds(
    session: Session, symbol: str = "SPX", *, as_of: date | None = None,
    edges: list[float] | None = None,
) -> dict[str, Any]:
    """Option-implied odds of where the front (next-to-settle) expiry closes, by bucket.
    Default edges: straddle breakevens, the gamma flip, and twice the straddle either side."""
    sym = symbol.upper()
    ts = pt._latest_oi_ts(session, sym, as_of)
    spot = pt._latest_spot(session, sym, as_of)
    if ts is None or spot is None:
        return {"symbol": sym, "found": False}
    floor = live_expiry_floor(ts.date())
    exp = session.execute(
        select(OiChainEod.expiry).where(OiChainEod.symbol == sym, OiChainEod.source.like("convex%"),
                                        OiChainEod.ts == ts, OiChainEod.expiry >= floor)
        .order_by(OiChainEod.expiry).limit(1)).scalar()
    if exp is None:
        return {"symbol": sym, "found": False}
    rows = session.execute(
        select(OiChainEod.strike, OiChainEod.cp, OiChainEod.iv, OiChainEod.dte).where(
            OiChainEod.symbol == sym, OiChainEod.source.like("convex%"), OiChainEod.ts == ts,
            OiChainEod.expiry == exp, OiChainEod.iv.isnot(None))).all()
    smile: dict[float, float] = {}
    for r in rows:   # out-of-the-money side: puts below spot, calls above
        k, iv = float(r.strike), float(r.iv)
        if not (0.01 < iv < 5.0):
            continue
        if (str(r.cp).upper().startswith("P") and k <= spot) or (str(r.cp).upper().startswith("C") and k > spot):
            smile[k] = iv
    if len(smile) < 5:
        return {"symbol": sym, "found": False, "reason": "too few strikes with IV"}
    dte = (exp - ts.date()).days
    t = same_day_years(ts.date()) if dte <= 0 else dte / 365.0
    ks = np.array(sorted(smile))
    vs = np.array([smile[x] for x in ks])
    atm = float(np.interp(spot, ks, vs))
    straddle = float(2 * bs_call_price(spot, np.array([spot]), np.array([atm]), np.array([t]))[0])
    flip = None
    if edges is None:
        prof = pt.get_profile(session, sym, span=0.03, n_points=121, as_of=as_of, greeks=("gamma",))
        flip = (prof.get("profiles") or {}).get("gamma", {}).get("flip") if prof.get("found") else None
        e = {round(spot - 2 * straddle), round(spot - straddle), round(spot + straddle), round(spot + 2 * straddle)}
        if flip and abs(flip - spot) < 3 * straddle:
            e.add(round(flip))
        edges = sorted(e)
    p = odds_from_smile(ks, vs, spot, t, edges)
    labels = [f"below {edges[0]:,.0f}"] + [f"{edges[i]:,.0f} to {edges[i + 1]:,.0f}"
                                            for i in range(len(edges) - 1)] + [f"above {edges[-1]:,.0f}"]
    return {"symbol": sym, "as_of": ts.date().isoformat(), "expiry": exp.isoformat(), "spot": round(spot, 2),
            "straddle": round(straddle, 2), "gamma_flip": round(flip, 1) if flip else None,
            "buckets": [{"range": lab, "odds_pct": round(100 * x, 1)} for lab, x in zip(labels, p, strict=True)],
            "found": True, "note": "risk-neutral odds from the book's own smile (r = 0); not a forecast"}


# --------------------------------------------------------------------- event days
def event_day_rows(dates: list[date], quotes: dict[date, tuple[float, float, float]],
                   gex: dict[date, float]) -> list[dict[str, Any]]:
    """One row per event day that has prices: range % of prior close, close-to-close %, and the naive
    net gamma at the prior close (latest book dated before the event)."""
    qd = sorted(quotes)
    gd = sorted(gex)
    out = []
    for d in dates:
        prev = [x for x in qd if x < d]
        if d not in quotes or not prev:
            continue
        pc = quotes[prev[-1]][2]
        hi, lo, cl = quotes[d]
        gprev = [x for x in gd if x < d]
        g = gex[gprev[-1]] if gprev else None
        out.append({"date": d.isoformat(), "range_pct": round(100 * (hi - lo) / pc, 2),
                    "move_pct": round(100 * (cl / pc - 1), 2), "net_gex_prior": g,
                    "gamma": None if g is None else ("long" if g > 0 else "short")})
    return out


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def agg(sub: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not sub:
            return None
        return {"n": len(sub), "avg_range_pct": round(sum(r["range_pct"] for r in sub) / len(sub), 2),
                "avg_move_pct": round(sum(r["move_pct"] for r in sub) / len(sub), 2),
                "up_days": sum(1 for r in sub if r["move_pct"] > 0)}
    return {"all": agg(rows), "long_gamma": agg([r for r in rows if r["gamma"] == "long"]),
            "short_gamma": agg([r for r in rows if r["gamma"] == "short"])}


def get_event_day_stats(
    session: Session, *, symbol: str = "SPX", dates: list[date] | None = None,
    fred: Any = None, since: date | None = None,  # noqa: ANN401 - FredClient (clients/), injected
) -> dict[str, Any]:
    """Past event days (CPI from FRED release 10 unless ``dates`` given) with SPX range and move, split
    by the naive dealer-gamma sign at the prior close (oi_chain_daily.net_gxoi)."""
    since = since or date(2024, 10, 1)
    if dates is None:
        if fred is None:
            return {"found": False, "reason": "no dates given and no FRED client"}
        dates = [d for d in fred.release_dates(FRED_CPI_RELEASE) if d >= since]
    dates = sorted(set(dates))
    if not dates:
        return {"found": False, "reason": "no event dates"}
    lo = dates[0] - timedelta(days=10)
    q = session.execute(select(QuoteDaily.date, QuoteDaily.high, QuoteDaily.low, QuoteDaily.close)
                        .where(QuoteDaily.symbol == symbol, QuoteDaily.date >= lo)).all()
    quotes = {r.date: (float(r.high), float(r.low), float(r.close)) for r in q}
    g = session.execute(select(OiChainDaily.ts, OiChainDaily.net_gxoi)
                        .where(OiChainDaily.symbol == symbol, OiChainDaily.ts >= lo)).all()
    gex = {r.ts: float(r.net_gxoi) for r in g if r.net_gxoi is not None}
    today = datetime.combine(date.today(), time.min).date()
    rows = event_day_rows([d for d in dates if d <= today], quotes, gex)
    return {"found": bool(rows), "symbol": symbol, "event": "CPI" if fred is not None else "custom",
            "rows": rows, "summary": _summary(rows),
            "note": "gamma = naive net gxoi sign at the prior close; our book has no history before it was "
                    "collected, so early dates may lack it"}


def register(mcp: Any, settings: Any, session_factory: Any) -> None:  # noqa: ANN401
    """Wire the three tools into the MCP server (called from server.py, same pattern as tenor_tools)."""

    def _d(x: str | None) -> date | None:
        return date.fromisoformat(x) if x else None

    @mcp.tool()
    def get_hedge_response(symbol: str = "SPX", as_of: str | None = None) -> dict[str, Any]:
        """ES contracts (thousands) dealers would trade for SPX -2/-1/-0.5/+0.5/+1/+2% moves off the
        book's spot, full book and without the front expiry. Naive book; read the shape. Not a signal."""
        with session_factory() as session:
            return globals()["get_hedge_response"](session, symbol, as_of=_d(as_of))

    @mcp.tool()
    def get_close_odds(symbol: str = "SPX", as_of: str | None = None,
                       edges: list[float] | None = None) -> dict[str, Any]:
        """Option-implied odds of where the front expiry settles, by price bucket (straddle breakevens,
        gamma flip, 2x straddle by default; or your own ``edges``). Risk-neutral, not a forecast."""
        with session_factory() as session:
            return globals()["get_close_odds"](session, symbol, as_of=_d(as_of), edges=edges)

    @mcp.tool()
    def get_event_day_stats(symbol: str = "SPX", dates: list[str] | None = None,
                            since: str | None = None) -> dict[str, Any]:
        """Past CPI days (FRED release 10) or your ``dates``: SPX day range and move, split by naive
        dealer gamma sign at the prior close. Descriptor only."""
        fred = None
        if not dates:
            from trading_intel.clients.fred import FredClient
            fred = FredClient(settings)
        ds = [date.fromisoformat(x) for x in dates] if dates else None
        with session_factory() as session:
            return globals()["get_event_day_stats"](session, symbol=symbol, dates=ds, fred=fred,
                                                     since=_d(since))
