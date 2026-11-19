"""clean_book.py - created 2026-11-14. Our own forward + implied vols for an options book, from its PRICES.

Why (2026-11-14, real SPX 08:35 books): the vendor IV column is unreliable before the open. On the Fri 11/13
book the call and put IV at the same strike disagree by 18-48 vol points on six expiries (0DTE by 7), and one
Nov-18 7,860 call carries IV 0.2% -> an absurd gamma that made get_walls call 7,860 the "call wall". The
bid/ask prices are sound: call minus put gives one consistent forward for every expiry (7,644.6-7,665 on
11/13, rising with carry; SPX opened 7,617 after a ~+53 overnight futures move).

So: forward per expiry from put-call parity on mid prices, IV per strike solved from the out-of-the-money
mid against that forward (puts below, calls above, both averaged right at the forward), time to settlement
from the book's own time (08:35 for the morning SPX book, 16:00 for an end-of-day book) to 16:00 ET for
PM-settled and 09:30 ET for AM-settled contracts (sources ending "_set"). r = q = 0 (<= ~45 days).
Pure functions on row dicts so a live chain (the 09:45 refresh) and a stored book go through the same code.
"""
from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any

import numpy as np
from scipy.special import ndtr

PM_SETTLE = time(16, 0)
AM_SETTLE = time(9, 30)
AM_BOOK_TIME = time(8, 35)
EOD_BOOK_TIME = time(16, 0)
YEAR_S = 365.0 * 86400.0


def _pdf(x: np.ndarray) -> np.ndarray:
    return np.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def b76(f: float | np.ndarray, k: np.ndarray, sig: np.ndarray, t: float, call: np.ndarray) -> np.ndarray:
    """Black-76 price with r = 0 (call where ``call`` is True, else put)."""
    sd = np.maximum(sig * math.sqrt(max(t, 1e-9)), 1e-12)
    d1 = (np.log(f / k) + 0.5 * sd * sd) / sd
    c = f * ndtr(d1) - k * ndtr(d1 - sd)
    return np.where(call, c, c - (f - k))


def b76_gamma(f: float | np.ndarray, k: np.ndarray, sig: np.ndarray, t: float) -> np.ndarray:
    sd = np.maximum(sig * math.sqrt(max(t, 1e-9)), 1e-12)
    d1 = (np.log(f / k) + 0.5 * sd * sd) / sd
    return _pdf(d1) / (f * sd)


def implied_vol(f: float, k: np.ndarray, price: np.ndarray, t: float, call: np.ndarray) -> np.ndarray:
    """Vectorised bisection on [0.5%, 400%]; NaN where the price is outside the no-arbitrage range."""
    lo = np.full(len(k), 0.005)
    hi = np.full(len(k), 4.0)
    intrinsic = np.where(call, np.maximum(f - k, 0.0), np.maximum(k - f, 0.0))
    ok = (price > intrinsic + 1e-6) & (price < np.where(call, f, k))
    for _ in range(64):
        mid = 0.5 * (lo + hi)
        too_high = b76(f, k, mid, t, call) > price
        hi = np.where(too_high, mid, hi)
        lo = np.where(too_high, lo, mid)
    out = 0.5 * (lo + hi)
    out[~ok] = np.nan
    return out


def settle_dt(expiry: date, am_settled: bool) -> datetime:
    return datetime.combine(expiry, AM_SETTLE if am_settled else PM_SETTLE)


def years(a: datetime, b: datetime) -> float:
    return max((b - a).total_seconds(), 60.0) / YEAR_S


@dataclass
class ExpirySmile:
    expiry: date
    am_settled: bool
    t: float                                   # years from the book's time to settlement
    forward: float | None
    strikes: np.ndarray = field(default_factory=lambda: np.array([]))
    ivs: np.ndarray = field(default_factory=lambda: np.array([]))
    oi_call: dict[float, float] = field(default_factory=dict)
    oi_put: dict[float, float] = field(default_factory=dict)
    mids: dict[tuple[float, str], float] = field(default_factory=dict)
    n_quotes: int = 0
    parity_spread: float | None = None         # spread of the forward estimates (pts) - small = consistent


def _mid(b: Any, a: Any) -> float | None:
    if b is None or a is None:
        return None
    b, a = float(b), float(a)
    if b <= 0 or a <= b or (a - b) > max(1.0, 0.5 * 0.5 * (a + b)):
        return None
    return 0.5 * (a + b)


def parity_forward(mids: dict[tuple[float, str], float]) -> tuple[float | None, float | None]:
    """K + C - P at the 5 strikes where the call and put mids are closest (nearest the money)."""
    pts = [(abs(mids[(k, "C")] - mids[(k, "P")]), k + mids[(k, "C")] - mids[(k, "P")])
           for (k, cp) in mids if cp == "C" and (k, "P") in mids]
    if len(pts) < 3:
        return None, None
    best = sorted(pts)[:5]
    fs = np.array([f for _, f in best])
    return float(np.median(fs)), float(fs.max() - fs.min())


def _smile_from_mids(mids: dict[tuple[float, str], float], f: float, t: float) -> tuple[np.ndarray, np.ndarray]:
    """IV per strike from out-of-the-money mids around the forward (both sides averaged within 0.2%)."""
    ks, px, call = [], [], []
    for (k, cp), m in mids.items():
        otm = (cp == "C" and k >= f) or (cp == "P" and k <= f)
        if otm or abs(k - f) <= 0.002 * f:
            ks.append(k)
            px.append(m)
            call.append(cp == "C")
    if not ks:
        return np.array([]), np.array([])
    k_arr = np.array(ks)
    iv = implied_vol(f, k_arr, np.array(px), t, np.array(call))
    good = np.isfinite(iv)
    by: dict[float, list[float]] = {}
    for k, v in zip(k_arr[good], iv[good], strict=True):
        by.setdefault(float(k), []).append(float(v))
    out_k = np.array(sorted(by))
    return out_k, np.array([float(np.mean(by[k])) for k in out_k])


def build_smiles(rows: Iterable[dict[str, Any]], book_dt: datetime) -> dict[tuple[date, bool], ExpirySmile]:
    """rows: dicts with expiry, strike, cp, oi, bid, ask, source. One smile per (expiry, AM-settled?) -
    AM-settled contracts (source ending "_set": the 3rd-Friday monthly and the SPXO weeklies) settle at the
    09:30 open, so they get their own time to settlement and their own forward."""
    groups: dict[tuple[date, bool], dict[str, Any]] = {}
    for r in rows:
        am = str(r.get("source") or "").endswith("_set")
        g = groups.setdefault((r["expiry"], am), {"mids": {}, "oc": {}, "op": {}})
        k, cp = float(r["strike"]), str(r["cp"]).upper()[:1]
        side = g["oc"] if cp == "C" else g["op"]
        side[k] = side.get(k, 0.0) + float(r.get("oi") or 0)
        m = _mid(r.get("bid"), r.get("ask"))
        if m is not None:
            g["mids"][(k, cp)] = m
    out: dict[tuple[date, bool], ExpirySmile] = {}
    for (exp, am), g in sorted(groups.items()):
        t = years(book_dt, settle_dt(exp, am))
        f, spread = parity_forward(g["mids"])
        sm = ExpirySmile(exp, am, t, f, oi_call=g["oc"], oi_put=g["op"], mids=g["mids"],
                         n_quotes=len(g["mids"]), parity_spread=spread)
        if f is not None:
            sm.strikes, sm.ivs = _smile_from_mids(g["mids"], f, t)
        out[(exp, am)] = sm
    return out


def front(smiles: dict[tuple[date, bool], ExpirySmile], floor: date) -> ExpirySmile | None:
    """First PM-settled expiry on/after ``floor`` with a forward and a smile."""
    c = [sm for (e, am), sm in sorted(smiles.items()) if e >= floor and not am and sm.forward
         and len(sm.strikes) >= 5]
    return c[0] if c else None


def atm_iv(sm: ExpirySmile) -> float | None:
    if sm.forward is None or len(sm.strikes) < 3:
        return None
    return float(np.interp(sm.forward, sm.strikes, sm.ivs))


def cm_atm_iv(smiles: dict[tuple[date, bool], ExpirySmile], days: float) -> float | None:
    """Constant-maturity ATM IV at ``days`` (calendar), interpolated in total variance between the two
    PM-settled expiries around it."""
    pts = sorted((sm.t, atm_iv(sm)) for sm in smiles.values() if not sm.am_settled and atm_iv(sm))
    if len(pts) < 2:
        return None
    tt = days / 365.0
    ts = np.array([p[0] for p in pts])
    w = np.array([p[0] * p[1] ** 2 for p in pts])
    if tt <= ts[0]:
        return float(pts[0][1])
    if tt >= ts[-1]:
        return float(pts[-1][1])
    return float(math.sqrt(np.interp(tt, ts, w) / tt))


def straddle_mid(sm: ExpirySmile) -> tuple[float | None, float | None]:
    """Real-price straddle (call mid + put mid) at the listed strike nearest the forward."""
    if sm.forward is None:
        return None, None
    ks = sorted({k for (k, cp) in sm.mids if (k, "C") in sm.mids and (k, "P") in sm.mids},
                key=lambda k: abs(k - sm.forward))
    if not ks:
        return None, None
    k = ks[0]
    return float(sm.mids[(k, "C")] + sm.mids[(k, "P")]), float(k)


def iv_disagreement(rows: Iterable[dict[str, Any]], forwards: dict[date, float]) -> dict[date, float]:
    """Vendor check: median |vendor call IV - vendor put IV| (vol pts) within 1% of each expiry's forward."""
    acc: dict[date, dict[float, dict[str, float]]] = {}
    for r in rows:
        if r.get("iv") is None or r["expiry"] not in forwards:
            continue
        acc.setdefault(r["expiry"], {}).setdefault(float(r["strike"]), {})[str(r["cp"]).upper()[:1]] = float(r["iv"])
    out = {}
    for e, d in acc.items():
        f = forwards[e]
        gaps = [abs(v["C"] - v["P"]) * 100 for k, v in d.items() if "C" in v and "P" in v and abs(k - f) <= 0.01 * f]
        if gaps:
            out[e] = float(np.median(gaps))
    return out


def gamma_curve(smiles: dict[tuple[date, bool], ExpirySmile], grid: np.ndarray,
                expiries: set[date] | None = None, include_am: bool = True) -> np.ndarray:
    """Dealer gamma ($bn per 1% move, naive sign: dealers long calls, short puts) at each price in ``grid``,
    our IVs held at each strike (sticky strike). ``expiries`` limits the sum; ``include_am`` False drops the
    AM-settled contracts (gone at the 09:30 open on their expiry day)."""
    tot = np.zeros(len(grid))
    for (e, am), p in smiles.items():
        if (expiries is not None and e not in expiries) or (am and not include_am):
            continue
        if p.forward is None or len(p.strikes) < 3:
            continue
        for oi_map, sign in ((p.oi_call, 1.0), (p.oi_put, -1.0)):
            ks = np.array([k for k, v in oi_map.items() if v > 0])
            if not len(ks):
                continue
            oi = np.array([oi_map[k] for k in ks])
            sig = np.interp(ks, p.strikes, p.ivs)
            g = b76_gamma(grid[:, None], ks[None, :], sig[None, :], p.t)
            tot += sign * (g * oi[None, :]).sum(1) * grid ** 2 * 0.01 * 100.0 / 1e9
    return tot


# ------------------------------------------------------------------ DB side (oi_chain_eod)
def book_rows(session: Any, symbol: str, ts: datetime, max_days: int = 45,
              strike_band: tuple[float, float] | None = None) -> list[dict[str, Any]]:
    """The book's rows as dicts (expiry, strike, cp, oi, bid, ask, source, iv) for expiries up to
    ``max_days`` after the book's day, optionally only strikes inside ``strike_band``."""
    from datetime import timedelta

    from sqlalchemy import select

    from trading_intel.memory.models import OiChainEod

    q = select(OiChainEod.expiry, OiChainEod.strike, OiChainEod.cp, OiChainEod.oi, OiChainEod.bid_price,
               OiChainEod.ask_price, OiChainEod.source, OiChainEod.iv).where(
        OiChainEod.symbol == symbol.upper(), OiChainEod.ts == ts, OiChainEod.source.like("convex%"),
        OiChainEod.expiry <= ts.date() + timedelta(days=max_days))
    if strike_band:
        q = q.where(OiChainEod.strike >= strike_band[0], OiChainEod.strike <= strike_band[1])
    return [{"expiry": r[0], "strike": float(r[1]), "cp": r[2], "oi": r[3] or 0, "bid": r[4], "ask": r[5],
             "source": r[6], "iv": r[7]} for r in session.execute(q).all()]


def book_time(kind: str, day: date) -> datetime:
    """When a book's prices were taken: the morning SPX pull 08:35, an end-of-day book 16:00."""
    return datetime.combine(day, AM_BOOK_TIME if kind == "am" else EOD_BOOK_TIME)


def recent_books(session: Any, symbol: str, before: datetime, n: int) -> list[datetime]:
    from sqlalchemy import select

    from trading_intel.memory.models import OiChainEod

    return list(session.execute(
        select(OiChainEod.ts).where(OiChainEod.symbol == symbol.upper(), OiChainEod.source.like("convex%"),
                                    OiChainEod.ts < before).distinct().order_by(OiChainEod.ts.desc()).limit(n)
    ).scalars().all())


def vol_history(session: Any, symbol: str, books: list[datetime], kind: str,
                ref: dict[date, float]) -> list[tuple[date, float | None, float | None]]:
    """(day, 7-day ATM IV, 30-day ATM IV) from our own IVs for each book - near-the-money strikes only."""
    out = []
    for ts in books:
        r = ref.get(ts.date())
        band = (0.96 * r, 1.04 * r) if r else None
        sms = build_smiles(book_rows(session, symbol, ts, max_days=40, strike_band=band), book_time(kind, ts.date()))
        out.append((ts.date(), cm_atm_iv(sms, 7), cm_atm_iv(sms, 30)))
    return out
