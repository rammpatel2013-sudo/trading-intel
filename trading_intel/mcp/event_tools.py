"""Event-day tools (2026-11-10, after the VolSignals CPI thread): hedge response, close odds by price bucket,
and how SPX moved on past event days split by the dealer-gamma sign at the prior close.

    get_hedge_response  - ES contracts dealers would trade for each SPX move off the book's spot, full book
                          and without the front expiry (the "shot clock": what is left after today's close).
    get_close_odds      - option-implied odds of where the front expiry settles, by price bucket
                          (Breeden-Litzenberger on the smile: P(S_T > K) = -dC/dK with r = 0).
                          2026-11-12: cleaned smile (two-sided quotes only, calls and puts
                          blended near the money, outliers dropped), survival kept falling by
                          a least-squares fit instead of a running minimum, priced at the
                          BOOK's own time, and a quality check that falls back to a plain
                          lognormal when the smile cannot be read (11/12 08:35 book: the old
                          maths put 80.6% of the odds in one 10-point bucket).
    get_event_day_stats - past event days (CPI by default, FRED release 10) with the SPX day range and
                          close-to-close move, split by the naive dealer-gamma sign at the prior close.

All naive dealer book (calls +, puts -) like get_profile; positioning reads, never signals (rule 4).
"""
from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.greeks.black_scholes import bs_call_price
from trading_intel.mcp import profile_tool as pt
from trading_intel.memory.models import GreeksSnapshot, OiChainDaily, OiChainEod, QuoteDaily
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
    mq = [e for e in by_exp if _is_monthly_or_quarterly(e)]
    ex_mq = list(full)
    for e in mq:
        ex_mq = [a - b for a, b in zip(ex_mq, by_exp[e], strict=True)]
    s0 = float(prof["spot"])
    rows = []
    for m in moves:
        rows.append({
            "move_pct": m, "spx": round(s0 * (1 + m / 100.0), 1),
            "es_k": round(hedge_from_curve(ref, full, s0, m) / 1000.0, 1),
            "es_k_ex_front": round(hedge_from_curve(ref, ex_front, s0, m) / 1000.0, 1),
            "es_k_ex_monthly": round(hedge_from_curve(ref, ex_mq, s0, m) / 1000.0, 1),
        })
    return {"symbol": prof["symbol"], "as_of": prof["as_of"], "spot": s0, "found": True,
            "front_expiry": front, "monthly_quarterly_expiries": sorted(mq), "rows": rows,
            "units": "thousand ES contracts; negative = dealers sell, positive = buy",
            "note": ("naive book (dealers long calls, short puts); sticky strike. Its size runs "
                     "far above signed books (about 30x VolSignals' table for Nov 10), mostly "
                     "because every monthly and quarterly put counts as dealer-short; "
                     "es_k_ex_monthly drops those expiries to show how much they carry. "
                     "Read the shape, not the size.")}


def _is_monthly_or_quarterly(exp: Any) -> bool:  # noqa: ANN401 - ISO string or date
    """3rd-Friday monthly, or the last weekday of Mar/Jun/Sep/Dec (holidays ignored) - the expiries
    where big institutional put positions sit (same rule as scripts/vs_flip_score.py)."""
    try:
        d = exp if isinstance(exp, date) else date.fromisoformat(str(exp)[:10])
    except ValueError:
        return False
    if d.weekday() == 4 and 15 <= d.day <= 21:
        return True
    if d.month in (3, 6, 9, 12) and d.weekday() < 5:
        nxt = d + timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += timedelta(days=1)
        return nxt.month != d.month
    return False


# --------------------------------------------------------------------- close odds
def pava_down(y: np.ndarray) -> np.ndarray:
    """Least-squares fit that never rises (pool adjacent violators). The old running minimum let
    one bad strike drag every higher strike to its value - on the 11/12 08:35 book that collapsed
    the odds into two 10-point buckets."""
    vals: list[float] = []
    wts: list[float] = []
    lens: list[int] = []
    for v in np.asarray(y, float)[::-1]:        # fit non-decreasing on the reversed series
        vals.append(float(v))
        wts.append(1.0)
        lens.append(1)
        while len(vals) > 1 and vals[-2] > vals[-1]:
            w = wts[-2] + wts[-1]
            vals[-2:] = [(vals[-2] * wts[-2] + vals[-1] * wts[-1]) / w]
            wts[-2:] = [w]
            lens[-2:] = [lens[-2] + lens[-1]]
    return np.repeat(np.asarray(vals), lens)[::-1]


def survival_from_smile(strikes: np.ndarray, ivs: np.ndarray, spot: float,
                        t: float) -> tuple[np.ndarray, np.ndarray]:
    """(grid, P(S_T > grid)) from a smile (strike -> IV), r = q = 0. The grid reaches 6 ATM sigmas
    past the quoted strikes (IV held flat there) so both tails are counted."""
    o = np.argsort(strikes)
    k, v = np.asarray(strikes, float)[o], np.asarray(ivs, float)[o]
    sd = float(np.interp(spot, k, v)) * math.sqrt(max(t, 1e-9))
    lo, hi = min(k[0], spot * math.exp(-6 * sd)), max(k[-1], spot * math.exp(6 * sd))
    grid = np.linspace(lo, hi, 4001)
    sig = np.interp(grid, k, v)
    c = bs_call_price(spot, grid, sig, np.full_like(grid, t))
    surv = np.clip(pava_down(-np.gradient(c, grid)), 0.0, 1.0)
    return grid, surv


def odds_from_smile(strikes: np.ndarray, ivs: np.ndarray, spot: float, t: float,
                    edges: list[float]) -> list[float]:
    """P(S_T in each bucket between consecutive ``edges``) from a smile (strike -> IV), r = q = 0.
    Survival P(S_T > K) = -dC/dK on a dense strike grid; buckets below the first / above the last edge
    are included as the first / last entries (len = len(edges) + 1)."""
    grid, surv = survival_from_smile(strikes, ivs, spot, t)
    return _buckets(grid, surv, edges)


def _buckets(grid: np.ndarray, surv: np.ndarray, edges: list[float]) -> list[float]:
    sv = [float(np.interp(e, grid, surv, left=1.0, right=0.0)) for e in edges]
    out = [1.0 - sv[0]] + [sv[i] - sv[i + 1] for i in range(len(sv) - 1)] + [sv[-1]]
    return [max(0.0, x) for x in out]


def clean_smile(rows: list[tuple[float, str, float | None, float | None, float | None]],
                spot: float) -> tuple[np.ndarray, np.ndarray, dict[str, int]]:
    """Strike -> IV for the close odds, from (strike, cp, iv, bid, ask) rows.
    - quotes must be two-sided when the book carries them (bid > 0, ask > bid);
    - out-of-the-money side (puts below spot, calls above), the two sides averaged within 0.5%
      of spot so the switch at spot leaves no step;
    - a strike whose IV sits far from its 4 neighbours' median is dropped (stale or bad print);
    - one light 3-point average."""
    calls: dict[float, float] = {}
    puts: dict[float, float] = {}
    dropped = {"one_sided": 0, "outlier": 0}
    for k, cp, iv, bid, ask in rows:
        if iv is None or not (0.01 < float(iv) < 5.0):
            continue
        if bid is not None and ask is not None and (float(bid) <= 0 or float(ask) <= float(bid)):
            dropped["one_sided"] += 1
            continue
        (calls if str(cp).upper().startswith("C") else puts)[float(k)] = float(iv)
    band = 0.005 * spot
    pts: list[tuple[float, float]] = []
    for k in sorted(set(calls) | set(puts)):
        c, p = calls.get(k), puts.get(k)
        if abs(k - spot) <= band and c is not None and p is not None:
            pts.append((k, 0.5 * (c + p)))
        elif k <= spot and p is not None:
            pts.append((k, p))
        elif k > spot and c is not None:
            pts.append((k, c))
    if len(pts) < 5:
        return np.array([]), np.array([]), dropped
    ks = np.array([x[0] for x in pts])
    vs = np.array([x[1] for x in pts])
    keep = np.ones(len(vs), bool)
    for i in range(len(vs)):
        nb = np.concatenate([vs[max(0, i - 2):i], vs[i + 1:i + 3]])
        med = float(np.median(nb)) if len(nb) else vs[i]
        if abs(vs[i] - med) > max(0.30 * med, 0.05):
            keep[i] = False
    dropped["outlier"] = int((~keep).sum())
    ks, vs = ks[keep], vs[keep]
    if len(vs) >= 3:
        sm = vs.copy()
        sm[1:-1] = (vs[:-2] + vs[1:-1] + vs[2:]) / 3.0
        vs = sm
    return ks, vs, dropped


def dist_stats(grid: np.ndarray, surv: np.ndarray, spot: float,
               straddle: float) -> dict[str, float]:
    """Median, P10 / P90, the biggest 5-point bucket and the odds inside spot +/- one straddle."""
    cdf = 1.0 - surv
    q = lambda p: float(np.interp(p, cdf, grid))  # noqa: E731 - cdf is non-decreasing
    b = np.arange(math.floor(grid[0] / 5) * 5, grid[-1] + 5, 5.0)
    sb = np.interp(b, grid, surv, left=1.0, right=0.0)
    return {"median": q(0.5), "p10": q(0.10), "p90": q(0.90),
            "max_5pt": float(np.max(-np.diff(sb))) if len(sb) > 1 else 1.0,
            "in_1_straddle": float(np.interp(spot - straddle, grid, surv)
                                   - np.interp(spot + straddle, grid, surv))}


def odds_quality(st: dict[str, float], spot: float, straddle: float) -> list[str]:
    """Reasons the smile's odds cannot be trusted (empty = fine). A lognormal with the same width
    puts ~79% inside one straddle, ~8% at most in any 5-point bucket for a 25-point straddle, and
    its middle at spot."""
    why = []
    if abs(st["median"] - spot) > max(straddle, 10.0):
        why.append(f"middle {st['median']:,.0f} sits more than one straddle from spot")
    if st["max_5pt"] > 0.25:
        why.append(f"{100 * st['max_5pt']:.0f}% of the odds in one 5-point bucket")
    if not (0.45 <= st["in_1_straddle"] <= 0.95):
        why.append(f"{100 * st['in_1_straddle']:.0f}% inside one straddle "
                   "(a normal day is ~70-80%)")
    return why


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
        select(OiChainEod.strike, OiChainEod.cp, OiChainEod.iv, OiChainEod.bid_price,
               OiChainEod.ask_price).where(
            OiChainEod.symbol == sym, OiChainEod.source.like("convex%"), OiChainEod.ts == ts,
            OiChainEod.expiry == exp, OiChainEod.iv.isnot(None))).all()
    near = _spot_near(session, sym, ts)
    if near is not None:
        spot = near
    ks, vs, dropped = clean_smile(
        [(r.strike, r.cp, r.iv, r.bid_price, r.ask_price) for r in rows], spot)
    if len(ks) < 5:
        return {"symbol": sym, "found": False, "reason": "too few two-sided strikes with IV"}
    dte = (exp - ts.date()).days
    # priced at the BOOK's own time (its IVs and spot), not at the time the tool is asked
    t = same_day_years(ts.date(), now=ts) if dte <= 0 else dte / 365.0
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
    grid, surv = survival_from_smile(ks, vs, spot, t)
    stats = dist_stats(grid, surv, spot, straddle)
    why = odds_quality(stats, spot, straddle)
    quality = "smile"
    if why:   # the smile cannot be read: same width, plain lognormal from the ATM IV
        flat = (np.array([ks[0], spot, ks[-1]]) if ks[0] < spot < ks[-1]
                else np.array([spot * 0.8, spot, spot * 1.2]))
        grid, surv = survival_from_smile(flat, np.full(3, atm), spot, t)
        stats = dist_stats(grid, surv, spot, straddle)
        quality = "fallback_lognormal"
    p = _buckets(grid, surv, edges)
    labels = [f"below {edges[0]:,.0f}"] + [f"{edges[i]:,.0f} to {edges[i + 1]:,.0f}"
                                            for i in range(len(edges) - 1)] + [f"above {edges[-1]:,.0f}"]
    return {"symbol": sym, "as_of": ts.date().isoformat(),
            "book_ts": ts.isoformat(timespec="minutes"),
            "expiry": exp.isoformat(), "spot": round(spot, 2), "t_hours": round(t * 8760.0, 2),
            "straddle": round(straddle, 2), "gamma_flip": round(flip, 1) if flip else None,
            "gamma_flip_def": ("price where dealer gamma from every expiry in this book (naive "
                               "sign: dealers long calls, short puts) adds to zero, re-priced on a "
                               "+/-3% grid"),
            "buckets": [{"range": lab, "odds_pct": round(100 * x, 1)} for lab, x in zip(labels, p, strict=True)],
            "median": round(stats["median"], 1), "p10": round(stats["p10"], 1),
            "p90": round(stats["p90"], 1), "quality": quality, "quality_reasons": why,
            "strikes_used": len(ks), "dropped": dropped,
            "found": True,
            "note": ("risk-neutral odds from the book's own smile at the book's time (r = 0); "
                     "not a forecast"
                     + ("" if quality == "smile" else
                        "; the smile could not be read, so these are plain lognormal odds "
                        "from the ATM IV"))}


def _spot_near(session: Session, sym: str, ts: datetime) -> float | None:
    """Spot from the latest convex greeks snapshot at or before the book (+30 min), so a morning
    book is priced at its own spot, not a later intraday one."""
    row = session.execute(
        select(GreeksSnapshot.spot).where(GreeksSnapshot.symbol == sym,
                                          GreeksSnapshot.source == "convex",
                                          GreeksSnapshot.ts <= ts + timedelta(minutes=30))
        .order_by(GreeksSnapshot.ts.desc())).first()
    return float(row[0]) if row and row[0] is not None else None


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
