# ruff: noqa: E501
"""week_map.py - created 2026-11-20 for Mithil. The SPX week map and month map (VolSignals-style).

One options book (the 08:35 SPX pull, market.clean_book: our own forward + our own IVs) -> for every
trading day up to the target expiry (this week's Friday, or the monthly 3rd Friday):

  - gamma flip and near-dated vanna flip, re-priced with the time left that day,
  - THE LINE (the strike where dealers are shortest gamma) and the biggest dealer-long strikes above and
    below it (fixed from the book),
  - decay zones: where time decay alone makes dealers BUY (green) or SELL (pink) during that day,
  - three illustrated paths - vol down / unchanged / up (VOL_STEP vol points per day): each day the vol
    move changes the dealers' delta (vanna); they re-hedge, price goes that way until their book is back
    where it was (long gamma = a stop) and never more than one day's 1-sigma. Decay is the same for all
    three paths and is drawn as the zones, so "vol unchanged" stays put (as on the VolSignals map),
  - the 1-sigma cone from the forward, and the 09:30 AM-settlement marker on a monthly / SPXO expiry.

Dealer side is NAIVE (dealers long the calls, short the puts - same as the 0DTE card). Paths are
illustrations, not forecasts. Descriptor, not a signal (rule 4). Everything is rebuildable from
oi_chain_eod, so the nightly score rebuilds a past map instead of storing it (no new table).
"""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from typing import Any

import numpy as np
from scipy.special import ndtr

from trading_intel.market import clean_book as cb
from trading_intel.timeutils import is_trading_session

VOL_STEP = 2.0                      # vol points per day for the vol-up / vol-down paths
VOL_CAP = 8.0                       # ... up to this many points in all (a month map stops adding vol after 4 days)
SCENARIOS = (("down", -1.0), ("flat", 0.0), ("up", 1.0))


def vol_shift(sign: float, days_done: int) -> float:
    """Vol points added by the end of day ``days_done`` (1 = first close) in a scenario."""
    return sign * min(VOL_STEP * days_done, VOL_CAP)
GRID_PCT = 0.08                     # price grid = forward +/- 8%
GRID_N = 321
FULL_FLOW_USD = 1.5e9               # a vol re-hedge this big (or bigger) can move price a full day's 1-sigma
CLOSE = time(16, 0)
LAST_CLOSE = time(15, 45)           # on the expiry day the PM contracts are still alive at 15:45
DEALER_SIGN = "naive: dealers long the calls, short the puts"
_SQ2PI = math.sqrt(2.0 * math.pi)


# ------------------------------------------------------------------ calendar
def _back_to_session(d: date) -> date:
    for _ in range(5):
        if is_trading_session(d):
            return d
        d -= timedelta(days=1)
    return d


def weekly_target(day: date) -> date:
    """This week's Friday; on a Friday (that is today's 0DTE) the next week's. Holiday -> day before."""
    ahead = (4 - day.weekday()) % 7 or 7
    return _back_to_session(day + timedelta(days=ahead))


def third_friday(y: int, m: int) -> date:
    d = date(y, m, 15)
    return d + timedelta(days=(4 - d.weekday()) % 7)


def monthly_target(day: date) -> date:
    """The monthly (3rd Friday) after ``day``; on the 3rd Friday itself, next month's."""
    t = third_friday(day.year, day.month)
    if t <= day:
        y, m = (day.year + 1, 1) if day.month == 12 else (day.year, day.month + 1)
        t = third_friday(y, m)
    return _back_to_session(t)


def session_days(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if is_trading_session(d):
            out.append(d)
        d += timedelta(days=1)
    return out


# ------------------------------------------------------------------ the book as arrays
class Book:
    """Every contract expiring on/before the target: strike, our IV, dealer weight (+call OI, -put OI)."""

    def __init__(self, smiles: dict, target: date) -> None:
        ks, sig, w, call, settle = [], [], [], [], []
        for (e, am), sm in smiles.items():
            if e > target or sm.forward is None or len(sm.strikes) < 3:
                continue
            st = cb.settle_dt(e, am)
            for oi_map, is_call in ((sm.oi_call, True), (sm.oi_put, False)):
                for k, oi in oi_map.items():
                    if oi > 0:
                        ks.append(k)
                        sig.append(float(np.interp(k, sm.strikes, sm.ivs)))
                        w.append(oi if is_call else -oi)
                        call.append(is_call)
                        settle.append(st)
        self.k = np.array(ks, dtype=float)
        self.sig = np.clip(np.array(sig, dtype=float), 0.03, 3.0)
        self.w = np.array(w, dtype=float)
        self.call = np.array(call, dtype=bool)
        self.settle = settle

    def __len__(self) -> int:
        return len(self.k)

    def _t(self, at: datetime) -> np.ndarray:
        return np.array([(s - at).total_seconds() / cb.YEAR_S for s in self.settle])

    def greeks(self, s: np.ndarray, at: datetime, dvol: float = 0.0,
               alive_at: datetime | None = None) -> dict[str, np.ndarray]:
        """Dealer delta (shares), gamma ($bn per 1%), vanna (shares per vol pt) at each price in ``s``.
        ``alive_at``: keep only the contracts still alive at that later time (a day's hedge flow compares
        the same contracts at both ends - one that settles in between is gone, not decayed)."""
        t = self._t(at)
        alive = (t > 0) if alive_at is None else (self._t(alive_at) > 0)
        k, w, c = self.k[alive], self.w[alive], self.call[alive]
        sig = np.maximum(self.sig[alive] + dvol / 100.0, 0.03)
        tt = t[alive]
        if not len(k):
            z = np.zeros(len(s))
            return {"delta": z, "gamma": z, "vanna": z}
        sq = sig * np.sqrt(tt)
        d1 = (np.log(s[:, None] / k[None, :]) + 0.5 * (sig * sig * tt)[None, :]) / sq[None, :]
        d2 = d1 - sq[None, :]
        pdf = np.exp(-0.5 * d1 * d1) / _SQ2PI
        dl = ndtr(d1) - (~c)[None, :]
        delta = (dl * w[None, :]).sum(1) * 100.0
        gam = (pdf / (s[:, None] * sq[None, :]) * w[None, :]).sum(1) * s ** 2 * 0.01 * 100.0 / 1e9
        vanna = (-pdf * d2 / sig[None, :] * w[None, :]).sum(1) * 0.01 * 100.0
        return {"delta": delta, "gamma": gam, "vanna": vanna}

    def strike_gamma(self, s: float, at: datetime) -> dict[float, float]:
        """Dealer gamma ($bn per 1%) by strike at price ``s``."""
        t = self._t(at)
        out: dict[float, float] = {}
        for k, sg, w, tt in zip(self.k, self.sig, self.w, t, strict=True):
            if tt <= 0:
                continue
            sq = sg * math.sqrt(tt)
            d1 = (math.log(s / k) + 0.5 * sg * sg * tt) / sq
            g = math.exp(-0.5 * d1 * d1) / _SQ2PI / (s * sq) * w * s * s * 0.01 * 100.0 / 1e9
            out[float(k)] = out.get(float(k), 0.0) + g
        return out


# ------------------------------------------------------------------ helpers
def _zero_near(grid: np.ndarray, y: np.ndarray, near: float) -> float | None:
    best = None
    for i in range(len(grid) - 1):
        a, b = y[i], y[i + 1]
        if (a < 0) != (b < 0) and a != b:
            x = float(grid[i] + (grid[i + 1] - grid[i]) * (-a) / (b - a))
            if best is None or abs(x - near) < abs(best - near):
                best = x
    return best


def _zones(grid: np.ndarray, flow: np.ndarray, floor: float) -> list[dict[str, Any]]:
    """Runs of the same sign: buy (+1) / sell (-1) / quiet (0, |flow| under ``floor``)."""
    sg = np.where(np.abs(flow) < floor, 0, np.sign(flow)).astype(int)
    out, i = [], 0
    while i < len(grid):
        j = i
        while j + 1 < len(grid) and sg[j + 1] == sg[i]:
            j += 1
        out.append({"lo": round(float(grid[i]), 1), "hi": round(float(grid[j]), 1), "side": int(sg[i])})
        i = j + 1
    return out


def _step(grid: np.ndarray, delta_new: np.ndarray, held: float, s0: float, cap: float) -> float:
    """Dealers held ``held`` shares of delta at ``s0``; after the vol move their book says
    ``delta_new``. They trade the gap (more delta -> sell -> price down). Price goes that way until their
    book is back at ``held`` (dealers long gamma soak it up) - or ``cap`` away (short gamma: no stop)."""
    gap = float(np.interp(s0, grid, delta_new)) - held
    if gap == 0:
        return s0
    d = -1.0 if gap > 0 else 1.0
    lo, hi = (s0, s0 + cap) if d > 0 else (s0 - cap, s0)
    pts = grid[(grid > lo) & (grid < hi)]
    pts = pts if d > 0 else pts[::-1]
    for x in pts:
        if np.sign(np.interp(x, grid, delta_new) - held) != np.sign(gap):
            return float(x)
    return float(s0 + d * cap)


def _levels(by_k: dict[float, float], centre: float, band: float) -> dict[str, Any]:
    near = {k: v for k, v in by_k.items() if abs(k - centre) <= band}
    if not near:
        return {"line": None, "longs_above": None, "longs_below": None}
    line = min(near, key=lambda k: near[k])
    up = {k: v for k, v in near.items() if k > line and v > 0}
    dn = {k: v for k, v in near.items() if k < line and v > 0}
    a = max(up, key=lambda k: up[k]) if up else None
    b = max(dn, key=lambda k: dn[k]) if dn else None

    def lv(k: float | None) -> dict[str, float] | None:
        return None if k is None else {"strike": k, "gamma_bn": round(float(near[k]), 3)}

    return {"line": lv(line) if near[line] < 0 else None, "longs_above": lv(a), "longs_below": lv(b)}


# ------------------------------------------------------------------ the map
def build_map(rows: list[dict[str, Any]], book_dt: datetime, target: date, *,
              spot: float | None = None, kind: str = "week") -> dict[str, Any]:
    """The map for ``target`` from one book (``rows`` as clean_book.book_rows, prices at ``book_dt``)."""
    sms = cb.build_smiles(rows, book_dt)
    fr = cb.front(sms, book_dt.date())
    if fr is None or fr.forward is None:
        return {"found": False, "reason": "no clean front-expiry prices in the book"}
    fwd = float(fr.forward)
    if spot and abs(fwd - spot) > 0.05 * spot:
        return {"found": False, "reason": f"forward {fwd:,.0f} is >5% from spot {spot:,.0f}"}
    book = Book(sms, target)
    if not len(book):
        return {"found": False, "reason": f"no open interest expiring by {target}"}
    days = session_days(book_dt.date(), target)
    cal_days = max((target - book_dt.date()).days, 1)
    atm = cb.cm_atm_iv(sms, cal_days) or cb.atm_iv(fr) or 0.18
    grid = np.linspace(fwd * (1 - GRID_PCT), fwd * (1 + GRID_PCT), GRID_N)
    day_sig = fwd * atm / math.sqrt(252.0)
    evals = [datetime.combine(d, LAST_CLOSE if d == target else CLOSE) for d in days]

    band = max(2.5 * fwd * atm * math.sqrt(len(days) / 252.0), 0.03 * fwd)
    by_k = book.strike_gamma(fwd, book_dt)
    levels = _levels(by_k, fwd, band)

    out_days: list[dict[str, Any]] = []
    paths = {n: [round(fwd, 1)] for n, _ in SCENARIOS}
    pos = {n: fwd for n, _ in SCENARIOS}
    prev_at = book_dt
    for i, (d, at) in enumerate(zip(days, evals, strict=True)):
        base = book.greeks(grid, at, 0.0)
        start = book.greeks(grid, prev_at, 0.0, alive_at=at)
        decay = -(base["delta"] - start["delta"]) * grid                  # $ dealers trade: + = buy
        floor = max(1e6, 0.02 * float(np.abs(decay).max()))
        frac = min(1.0, (at - prev_at).total_seconds() / (6.5 * 3600.0)) if i == 0 else 1.0
        cap = day_sig * math.sqrt(max(frac, 0.15))
        row = {
            "date": d.isoformat(), "label": f"{d:%a %m/%d}", "at": at.isoformat(timespec="minutes"),
            "gamma_flip": _r(_zero_near(grid, base["gamma"], pos["flat"])),
            "vanna_flip": _r(_zero_near(grid, base["vanna"], pos["flat"])),
            "decay_zones": _zones(grid, decay, floor),
            "decay_at_flat_musd": round(float(np.interp(pos["flat"], grid, decay)) / 1e6, 1),
            "cone": [round(fwd - fwd * atm * math.sqrt((i + frac) / 252.0), 1),
                     round(fwd + fwd * atm * math.sqrt((i + frac) / 252.0), 1)],
        }
        for n, dv in SCENARIOS:
            # the path moves on the VOL part only (vanna); decay is the same for all three paths and is
            # shown as the decay zones - so "vol unchanged" stays put, like the VolSignals map
            if dv == 0:
                paths[n].append(round(pos[n], 1))
                row[f"flow_{n}_musd"] = 0.0
                continue
            if vol_shift(dv, i + 1) == vol_shift(dv, i):
                paths[n].append(round(pos[n], 1))
                row[f"flow_{n}_musd"] = 0.0
                continue
            before = book.greeks(grid, at, vol_shift(dv, i))["delta"]
            after = book.greeks(grid, at, vol_shift(dv, i + 1))["delta"]
            held = float(np.interp(pos[n], grid, before))
            usd = -(float(np.interp(pos[n], grid, after)) - held) * pos[n]
            row[f"flow_{n}_musd"] = round(usd / 1e6, 1)
            pos[n] = _step(grid, after, held, pos[n], cap * min(1.0, abs(usd) / FULL_FLOW_USD))
            paths[n].append(round(pos[n], 1))
        out_days.append(row)
        prev_at = at

    am_settle = any(am and e == target for (e, am) in sms)
    return {
        "found": True, "kind": kind, "target": target.isoformat(), "book_time": book_dt.isoformat(timespec="minutes"),
        "forward": round(fwd, 2), "spot": round(spot, 2) if spot else None, "atm_iv": round(atm, 4),
        "vol_step_pts": VOL_STEP, "vol_cap_pts": VOL_CAP, "levels": levels, "days": out_days, "paths": paths,
        "am_settle": f"{target:%a %m/%d} 09:30" if am_settle else None,
        "n_contracts": len(book), "dealer_sign": DEALER_SIGN, "band_pts": round(band, 0),
        "ends": {n: paths[n][-1] for n in paths},
        "sources": {
            "book": f"SPX options book {book_dt:%a %m/%d %H:%M} (oi_chain_eod; OI = prior close)",
            "forward_ivs": "our forward (put-call parity) and our IVs from mid prices (market.clean_book)",
            "levels": f"dealer gamma by strike at the forward, expiries to {target}, within {band:,.0f} pts",
            "paths": f"vol {VOL_STEP:g} pts/day (max {VOL_CAP:g} in all) down / unchanged / up: dealers re-hedge the vanna delta; "
                     "price moves that way until their book is back where it was (long gamma stops it) "
                     "or 1 day's 1-sigma x (flow / $1.5bn, max 1) when nothing stops it; decay is shown as the zones",
            "cone": "forward x ATM IV (our IVs, matched to the days left) x sqrt(sessions/252)",
        },
        "note": "paths are illustrations, not forecasts; descriptor, not a signal",
    }


def _r(x: float | None) -> float | None:
    return None if x is None else round(x, 1)


# ------------------------------------------------------------------ DB side
def build_from_db(session: Any, which: str = "week", as_of: date | None = None) -> dict[str, Any]:  # noqa: ANN401
    """The week ("week") or month ("month") map from the latest SPX book (or ``as_of``'s)."""
    from trading_intel.market.book_spot import book_spot
    from trading_intel.mcp import profile_tool as pt

    ts = pt._latest_oi_ts(session, "SPX", as_of)
    if ts is None:
        return {"found": False, "reason": "no SPX options book"}
    bs = book_spot(session, "SPX", ts)
    kind = bs["kind"] if bs else "am"
    day = ts.date()
    target = weekly_target(day) if which == "week" else monthly_target(day)
    rows = cb.book_rows(session, "SPX", ts, max_days=(target - day).days + 1)
    out = build_map(rows, cb.book_time(kind, day), target, spot=float(bs["spot"]) if bs else None, kind=which)
    out["book_day"] = day.isoformat()
    return out


def score_day(session: Any, day: date) -> dict[str, Any] | None:  # noqa: ANN401
    """How the week map built from ``day``'s morning book did at ``day``'s close: each path's modeled
    close vs the real close, and the vol move that actually happened (next book's 7-day ATM IV)."""
    from sqlalchemy import func, select

    from trading_intel.memory.models import GreeksSnapshot

    m = build_from_db(session, "week", as_of=day)
    if not m.get("found") or not m["days"] or m["days"][0]["date"] != day.isoformat():
        return None
    hi = datetime.combine(day, time(23, 59))
    last = session.execute(select(func.max(GreeksSnapshot.ts)).where(
        GreeksSnapshot.symbol == "SPX", GreeksSnapshot.source == "convex",
        GreeksSnapshot.ts >= datetime.combine(day, time(15, 0)),
        GreeksSnapshot.ts <= hi)).scalar()
    if last is None:
        return None
    close = session.execute(select(GreeksSnapshot.spot).where(
        GreeksSnapshot.symbol == "SPX", GreeksSnapshot.source == "convex", GreeksSnapshot.ts == last)).scalar()
    if close is None:
        return None
    modeled = {n: m["paths"][n][1] for n in m["paths"]}
    best = min(modeled, key=lambda n: abs(modeled[n] - float(close)))
    return {"day": day.isoformat(), "close": round(float(close), 1), "modeled": modeled, "nearest": best,
            "miss_pts": {n: round(float(close) - v, 1) for n, v in modeled.items()},
            "forward": m["forward"], "cone": m["days"][0]["cone"]}
