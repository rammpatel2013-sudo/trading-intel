"""vix_map.py - created 2026-11-09. VIX dealer gamma + vanna map (VIX tab option "VIX map").

Data: the collector's VIX chain pull ("vix", every minute, already in raw_YYYYMMDD.ndjson.gz).
wide_feed.py reads the raw file anyway, so it hands each decoded VIX pull to VixDay.feed();
nothing here calls the data vendor. Read the same way cvx_live.vix_rows() reads it:
cvx_live.decode() -> {"VIX": {"spot", "exps": [(head, book)]}}, head = days since 1970-01-01,
book = {strike: (call, put)}. Forward per expiry = vixdash.forward(book) (the VIX tab's own
forward); time = vixdash.years_to_soq() (calendar, to the expiry-morning settlement).

MATHS (pure functions below, unit-tested in tests/cvxmap/test_vix_map.py)
  Black-76 on each expiry's forward F, r = 0, calendar T, the strike's own IV
  (out-of-the-money side vs F).
      d1 = (ln(F/K) + s^2 T / 2) / (s sqrt T),  d2 = d1 - s sqrt T
      gamma = phi(d1) / (F s sqrt T)       per 1 point of F
      vanna = -phi(d1) d2 / s              change of delta per 1.00 of vol (x 0.01 = per vol pt)
  A VIX level y on the map moves every forward by the same amount: F_e(y) = F_e + (y - VIX now).
  Dealer sign (default): dealers SHORT VIX calls, LONG VIX puts (customers buy calls as hedges
  and sell puts):  dealer gamma(y) = sum_e sum_K 100 * gamma * (N_put - N_call).
  The SPX-style naive sign (dealers long calls, short puts) is exactly the negative, so the page
  flips it. N = open interest or today's volume (one at a time). Units: $k of VIX exposure per
  VIX point, per 1-point move (gamma) and per +1 vol point (vanna). Positive dealer gamma =
  dealers sell VIX as it rises (dampens moves).

  2026-11-09 follow-ups (Mithil): positions basis = the SPX MAP's set
  OI | Vol | OI + vol | OI + signed. Stored per column, dealer-default sign:
  *o = open interest, *v = today's volume, and (new) *t = signed trades from the trade tags:
  a customer buy leaves the dealer short, a sell leaves the dealer long, so dealer contracts =
  (calls sold - calls bought) + (puts sold - puts bought). That part is MEASURED and does not
  depend on the Dealer side switch. The page combines them: OI = s*o, Vol = s*v,
  OI + vol = s*(o + v), OI + signed = s*o + t, with s = +1 (default) or -1 (SPX-style).
  Grid widened to at least VIX 10..50 (the page shows 10-25 by default).
  "px" = every minute's [time, VIX, front forward], so the VIX line moves minute by minute.

OUTPUT  data/vixmap_YYYYMMDD.json   one column every 5 minutes + the newest minute (live),
        and px every minute.
"""
from __future__ import annotations

import datetime
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

SQ2PI = math.sqrt(2.0 * math.pi)
COL_MIN = 5                     # one stored column every 5 minutes (+ the newest minute)
GRID_STEP = 0.25                # VIX points between map rows
SIGN_DEALER = (-1.0, 1.0)       # (calls, puts): dealers short calls, long puts  (default)
SIGN_NAIVE = (1.0, -1.0)        # SPX-style: dealers long calls, short puts
MULT = 100.0


# ---------------------------------------------------------------- pure maths
def phi(x: float) -> float:
    return math.exp(-0.5 * x * x) / SQ2PI


def ncdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def b76_d1d2(f: float, k: float, t: float, s: float) -> tuple[float, float]:
    sq = s * math.sqrt(t)
    d1 = (math.log(f / k) + 0.5 * s * s * t) / sq
    return d1, d1 - sq


def b76_price(f: float, k: float, t: float, s: float, kind: str) -> float:
    """Black-76, r = 0 (undiscounted)."""
    if not (f > 0 and k > 0 and t > 0 and s > 0):
        return max(0.0, (f - k) if kind == "C" else (k - f))
    d1, d2 = b76_d1d2(f, k, t, s)
    if kind == "C":
        return f * ncdf(d1) - k * ncdf(d2)
    return k * ncdf(-d2) - f * ncdf(-d1)


def b76_delta(f: float, k: float, t: float, s: float, kind: str) -> float:
    d1, _ = b76_d1d2(f, k, t, s)
    return ncdf(d1) if kind == "C" else ncdf(d1) - 1.0


def b76_gamma(f: float, k: float, t: float, s: float) -> float:
    """d2V/dF2 (same for calls and puts)."""
    if not (f > 0 and k > 0 and t > 0 and s > 0):
        return 0.0
    d1, _ = b76_d1d2(f, k, t, s)
    return phi(d1) / (f * s * math.sqrt(t))


def b76_vanna(f: float, k: float, t: float, s: float) -> float:
    """d2V/dF ds = d(delta)/d(sigma), per 1.00 of vol (same for calls and puts)."""
    if not (f > 0 and k > 0 and t > 0 and s > 0):
        return 0.0
    d1, d2 = b76_d1d2(f, k, t, s)
    return -phi(d1) * d2 / s


def strike_iv(k: float, f: float, civ: float, piv: float) -> float:
    """Out-of-the-money side vs the forward; the other side when that one is missing."""
    a, b = (civ, piv) if k >= f else (piv, civ)
    return a if a and a > 0 else (b if b and b > 0 else 0.0)


def _tags(r: tuple) -> tuple[float, float, float, float]:
    """(calls bought, calls sold, puts bought, puts sold) of a row; 0 without trade tags."""
    return (r[7], r[8], r[9], r[10]) if len(r) >= 11 else (0.0, 0.0, 0.0, 0.0)


def signed_pos(r: tuple) -> float:
    """Dealer contracts from today's tagged trades (customer buy -> dealer short, sell ->
    dealer long), calls + puts. Gamma and vanna are the same for a call and a put."""
    cb, cs, pb, ps = _tags(r)
    return (cs - cb) + (ps - pb)


def dealer_profile(exps: list[dict], levels: list[float], vix_now: float, basis: str = "oi",
                   sign: tuple[float, float] = SIGN_DEALER) -> tuple[list[float], list[float]]:
    """Dealer gamma and vanna ($k) at each VIX level.
    exps: [{"F": forward, "T": years,
            "rows": [(k, cOI, pOI, cV, pV, cIV, pIV[, cb, cs, pb, ps]), ...]}].
    basis: "oi" | "vol" | "oiv" (OI + vol, both with the assumed sign) | "ois" (OI with the
    assumed sign + signed trades, measured) | "sig" (signed trades only).
    Each expiry's forward moves by (level - vix_now)."""
    sc, sp = sign
    gam = [0.0] * len(levels)
    van = [0.0] * len(levels)
    for e in exps:
        f0, t = e["F"], e["T"]
        if not (f0 > 0 and t > 0):
            continue
        for r in e["rows"]:
            k, coi, poi, cv, pv, civ, piv = r[:7]
            po, pv_ = sc * (coi or 0.0) + sp * (poi or 0.0), sc * (cv or 0.0) + sp * (pv or 0.0)
            ps_ = signed_pos(r)
            pos = {"oi": po, "vol": pv_, "oiv": po + pv_, "ois": po + ps_, "sig": ps_}[basis]
            if not pos:
                continue
            s = strike_iv(k, f0, civ, piv)
            if not (s > 0 and k > 0):
                continue
            for i, y in enumerate(levels):
                f = f0 + (y - vix_now)
                if f <= 0:
                    continue
                gam[i] += pos * MULT * b76_gamma(f, k, t, s) / 1e3
                van[i] += pos * MULT * b76_vanna(f, k, t, s) * 0.01 / 1e3
    return gam, van


def zero_cross(levels: list[float], vals: list[float], ref: float) -> float | None:
    """Level where vals changes sign (linear in between) nearest to ref; None if it never does."""
    best = None
    for i in range(1, len(levels)):
        a, b = vals[i - 1], vals[i]
        if (a < 0 <= b) or (a > 0 >= b):
            if b == a:
                continue
            x = levels[i - 1] + (levels[i] - levels[i - 1]) * (0.0 - a) / (b - a)
            if best is None or abs(x - ref) < abs(best - ref):
                best = x
    return best


def grid_for(vix: float) -> list[float]:
    """Map rows for the day, fixed at the first column: from min(10, 0.6x the opening VIX)
    (never below 8) to max(50, 1.9x the opening VIX) - VIX jumps up more than down.
    2026-11-09: the page shows 10-25 by default and 10-35 / 10-50 / All on request."""
    lo = max(8.0, min(10.0, math.floor(vix * 0.6)))
    hi = max(50.0, math.ceil(vix * 1.9))
    n = round((hi - lo) / GRID_STEP)
    return [round(lo + GRID_STEP * i, 2) for i in range(n + 1)]


def _sig(x: float, n: int = 4) -> float:
    return 0.0 if not x else float(f"{x:.{n}g}")


# ---------------------------------------------------------------- chain -> expiries
def _f(x: object) -> float:
    try:
        return float(x)  # type: ignore[arg-type]
    except Exception:
        return 0.0


def expiries(dv: dict, now: datetime.datetime) -> list[dict]:
    """Decoded VIX pull -> [{"e", "F", "T", "rows"}] for live expiries with a forward and
    some OI or volume."""
    import vixdash  # the VIX tab's forward and clock

    out = []
    for head, book in dv.get("exps") or []:
        try:
            e = (datetime.date(1970, 1, 1) + datetime.timedelta(days=int(head))).isoformat()
        except Exception:
            e = str(head)[:10]
        try:
            t = vixdash.years_to_soq(e, now)
        except ValueError:
            t = 0.0
        if t <= 0:
            continue
        rows = []
        for k in sorted(book):
            c, p = book[k]
            rows.append((float(k), _f(c.get("oi")), _f(p.get("oi")), _f(c.get("volm")),
                         _f(p.get("volm")), _f(c.get("volatility")), _f(p.get("volatility")),
                         _f(c.get("volm_buy")), _f(c.get("volm_sell")),
                         _f(p.get("volm_buy")), _f(p.get("volm_sell"))))
        if not any(r[1] or r[2] or r[3] or r[4] for r in rows):
            continue
        f = vixdash.forward(book)
        if f is None or f <= 0:
            continue
        out.append({"e": e, "F": float(f), "T": t, "rows": rows})
    return out


def top_strikes(exps: list[dict]) -> dict:
    """Biggest call-OI and put-OI strikes, front expiry and all expiries summed by strike."""
    def best(rows: list[tuple], j: int) -> float | None:
        r = max(rows, key=lambda x: x[j], default=None)
        return r[0] if r and r[j] > 0 else None

    out: dict = {}
    if exps:
        out["front"] = {"call": best(exps[0]["rows"], 1), "put": best(exps[0]["rows"], 2)}
        agg: dict[float, list[float]] = {}
        for e in exps:
            for r in e["rows"]:
                a = agg.setdefault(r[0], [0.0, 0.0])
                a[0] += r[1]
                a[1] += r[2]
        rows = [(k, v[0], v[1]) for k, v in agg.items()]
        out["all"] = {"call": best(rows, 1), "put": best(rows, 2)}
    return out


def profiles(ex: list[dict], levels: list[float], vix: float) -> dict[str, list[float]]:
    """All twelve map rows in one pass (greeks once per strike and level): g/v x front/all x
    o (OI) / v (volume), dealer-default sign / t (signed trades, measured).
    Same numbers as dealer_profile() (checked in the tests)."""
    sc, sp = SIGN_DEALER
    keys = tuple(g + b + x for g in "gv" for b in "fa" for x in "ovt")
    acc = {key: [0.0] * len(levels) for key in keys}
    for j, e in enumerate(ex):
        f0, t = e["F"], e["T"]
        if not (f0 > 0 and t > 0):
            continue
        sqt = math.sqrt(t)
        for r in e["rows"]:
            k, coi, poi, cv, pv, civ, piv = r[:7]
            po, pv_, pt = sc * coi + sp * poi, sc * cv + sp * pv, signed_pos(r)
            if not (po or pv_ or pt):
                continue
            s = strike_iv(k, f0, civ, piv)
            if not (s > 0 and k > 0):
                continue
            ssq = s * sqt
            for i, y in enumerate(levels):
                f = f0 + (y - vix)
                if f <= 0:
                    continue
                d1 = (math.log(f / k) + 0.5 * s * s * t) / ssq
                ph = math.exp(-0.5 * d1 * d1) / SQ2PI
                g = MULT * ph / (f * ssq) / 1e3
                v = MULT * (-ph * (d1 - ssq) / s) * 0.01 / 1e3
                for b, pos in (("o", po), ("v", pv_), ("t", pt)):
                    if not pos:
                        continue
                    acc["ga" + b][i] += pos * g
                    acc["va" + b][i] += pos * v
                    if j == 0:
                        acc["gf" + b][i] += pos * g
                        acc["vf" + b][i] += pos * v
    return acc


def column(dv: dict, now: datetime.datetime, levels: list[float],
           ex: list[dict] | None = None) -> dict | None:
    """One map column: front expiry and all expiries, OI and volume basis, dealer-default sign."""
    vix = _f(dv.get("spot"))
    if ex is None:
        ex = expiries(dv, now)
    if not (vix > 0 and ex):
        return None
    col = {"t": now.strftime("%H:%M"), "x": round(vix, 2), "fF": round(ex[0]["F"], 3),
           "fe": ex[0]["e"], "ne": len(ex), "top": top_strikes(ex)}
    for key, arr in profiles(ex, levels, vix).items():
        col[key] = [_sig(x) for x in arr]
    return col


class VixDay:
    """Day state inside wide_feed: one column per 5 minutes + the newest minute."""

    def __init__(self, ymd: str) -> None:
        self.ymd = ymd
        self.levels: list[float] | None = None
        self.cols: list[dict] = []
        self.last: dict | None = None
        self.exps: list[dict] = []
        self.px: list[list] = []        # every minute: [HH:MM, VIX, front forward]
        self.changed = False

    def feed(self, dv: dict, now: datetime.datetime) -> bool:
        t = now.strftime("%H:%M")
        if t < "09:30" or t > "16:15":
            return False
        vix = _f(dv.get("spot"))
        if not vix > 0:
            return False
        if self.levels is None:
            self.levels = grid_for(vix)
        ex = expiries(dv, now)
        col = column(dv, now, self.levels, ex)
        if col is None:
            return False
        pt = [t, col["x"], col["fF"]]
        if self.px and self.px[-1][0] == t:
            self.px[-1] = pt
        else:
            self.px.append(pt)
        if (now.hour * 60 + now.minute) % COL_MIN == 0 or not self.cols:
            if self.cols and self.cols[-1]["t"] == t:
                self.cols[-1] = col
            else:
                self.cols.append(col)
            self.last = None
        else:
            self.last = col
        self.exps = [{"e": e["e"], "F": round(e["F"], 3), "T_days": round(e["T"] * 365.0, 2)}
                     for e in ex]
        self.changed = True
        return True

    def payload(self) -> dict:
        cols = list(self.cols)
        if self.last is not None:
            cols.append(dict(self.last, live=1))
        return {"v": 2, "day": f"{self.ymd[:4]}-{self.ymd[4:6]}-{self.ymd[6:]}",
                "levels": self.levels or [], "col_min": COL_MIN, "units": "$k per VIX pt",
                "sign": "dealers short calls, long puts (flip for the naive sign)",
                "exps": self.exps, "cols": cols, "px": self.px,
                "bases": "o = open interest, v = today's volume (dealer-default sign); "
                         "t = signed trades (measured: calls sold - bought + puts sold - bought), "
                         "not flipped by the sign switch"}
