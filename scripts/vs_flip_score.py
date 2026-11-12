#!/usr/bin/env python
# ruff: noqa: E501, N806, ANN401, RUF100
"""vs_flip_score.py - created 2026-11-10 for Mithil (VS analysis plan, step 1).

How close does OUR SPX gamma flip get to VolSignals'? Scores every flip / gamma reading he has
published (scripts/vs_flip_refs.json) against our own book built several ways, so we learn which
way of building the book lands within 10 points of his.

Methods (each one = a different guess at the market-maker position):
  naive_stale  close refs only: the book we had at 17:15 that day. Its open interest is one day old
               (it misses that day's trades). This is how we got 7,643 vs his 7,594 on Nov 9.
  naive        open interest that already includes the day's trades (next session's book).
               Calls = dealers long, puts = dealers short.
  doi_div      naive, but on the monthly / quarterly expiries the LAST day's new contracts are signed
               by what implied vol did: OI up + IV down = customers sold (dealers long),
               OI up + IV up = customers bought (dealers short).
  doi_ledger   same rule run day by day over every stored day for the monthly / quarterly expiries,
               so the whole monthly position is signed, not just one day's adds.
  tape         close refs only: start-of-day book + that day's prints from tas_prints, each signed
               by its own quote or the tick rule (customer buy = dealer short).
  tape_pkg     tape + spread legs signed by reading the package together (sides_feed rules).

Pass rule (plan, 2026-11-10): within 10 points on at least 70% of his flip levels. Exact numbers he
printed and levels read off his charts are counted separately; intraday chart reads are shown but
never counted (his chart includes that day's trades, an OI book cannot).

Read-only: DB reads, no vendor calls, no cockpit files touched. Writes reports/vs_flip/.
Run on the NAS:
    sudo bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_script.sh vs_flip_score
    ... vs_flip_score --telegram          (also send the summary to Telegram)
    ... vs_flip_score --only nov09_full   (one reference, prints detail)
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

REFS = HERE / "vs_flip_refs.json"
OUT_DIR = REPO / "reports" / "vs_flip"
PASS_PTS = 10.0
PASS_SHARE = 0.70
SPAN = 0.07            # profile grid: spot +/- 7%
STEP = 1.0             # 1 SPX point
MULT = 100.0
YEAR_S = 365.0 * 86400.0
MIN_T_S = 3600.0       # 1-hour floor, same as the BS module
ROOTS = ("SPX", "SPXW", "SPXO")
METHODS = ("naive_stale", "naive", "doi_div", "doi_ledger", "tape", "tape_pkg")
COUNTED = ("exact", "approx")


# ---------------------------------------------------------------- pure logic (tests/test_vs_flip_score.py)
def is_monthly_or_quarterly(d: date) -> bool:
    """3rd-Friday monthly, or the last weekday of Mar/Jun/Sep/Dec (holidays ignored)."""
    if d.weekday() == 4 and 15 <= d.day <= 21:
        return True
    if d.month in (3, 6, 9, 12) and d.weekday() < 5:
        nxt = d + timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += timedelta(days=1)
        return nxt.month != d.month
    return False


def expiry_dt(exp: date, am: bool) -> datetime:
    return datetime.combine(exp, time(9, 30) if am else time(16, 0))


def years_left(exp: datetime, ref: datetime) -> float:
    return max((exp - ref).total_seconds(), MIN_T_S) / YEAR_S


def naive_sign(cp: str) -> float:
    return 1.0 if str(cp).upper().startswith("C") else -1.0


def doi_div_weight(cp: str, oi0: float, oi1: float, div: float | None) -> float:
    """Dealer contracts after one day: yesterday's OI signed naive, today's change signed by IV.

    New contracts (OI up): IV down = customers sold = dealers long (+); IV up = customers bought =
    dealers short (-); IV flat/unknown = naive sign. Closed contracts (OI down) leave the naive book."""
    ns = naive_sign(cp)
    d = oi1 - oi0
    if d <= 0:
        return ns * oi1
    if div is None or div == 0 or not math.isfinite(div):
        s = ns
    else:
        s = 1.0 if div < 0 else -1.0
    return ns * oi0 + s * d


def ledger_step(pos: float, cp: str, oi0: float, oi1: float, div: float | None) -> float:
    """One day of the monthly ledger. Adds signed by IV (as doi_div_weight); closes shrink the
    position toward zero in proportion, keeping its sign."""
    d = oi1 - oi0
    if d > 0:
        if div is None or div == 0 or not math.isfinite(div):
            s = naive_sign(cp)
        else:
            s = 1.0 if div < 0 else -1.0
        return pos + s * d
    if oi0 <= 0:
        return 0.0
    return pos * (oi1 / oi0)


def crossings(grid: np.ndarray, tot: np.ndarray) -> list[tuple[float, str]]:
    """Every sign change of the gamma profile: (level, 'up' = short->long as SPX rises, 'down')."""
    out: list[tuple[float, str]] = []
    for i in range(1, len(grid)):
        a, b = tot[i - 1], tot[i]
        if a < 0 <= b and b != a:
            out.append((float(np.interp(0.0, [a, b], [grid[i - 1], grid[i]])), "up"))
        elif a > 0 >= b and b != a:
            out.append((float(np.interp(0.0, [b, a], [grid[i], grid[i - 1]])), "down"))
    return out


def nearest(cross: list[tuple[float, str]], level: float, dirn: str) -> float | None:
    same = [x for x, d in cross if d == dirn]
    return min(same, key=lambda x: abs(x - level)) if same else None


def sign_ok(want: str, val: float, tol: float = 0.25) -> bool:
    """want '+', '-' or '<=0' (negative or neutral; 'neutral' = within tol $bn of zero)."""
    if want == "+":
        return val > 0
    if want == "-":
        return val < 0
    if want == "<=0":
        return val <= tol
    return False


def verdict(errors: list[float | None]) -> dict[str, Any]:
    """Share of references within PASS_PTS. A missing crossing counts as a miss."""
    n = len(errors)
    hits = sum(1 for e in errors if e is not None and e <= PASS_PTS)
    share = hits / n if n else None
    good = [e for e in errors if e is not None]
    return {
        "n": n, "within_10": hits, "share": None if share is None else round(share, 3),
        "median_err": None if not good else round(float(np.median(good)), 1),
        "pass": bool(n and share is not None and share >= PASS_SHARE),
    }


def gamma_profile(K: np.ndarray, sig: np.ndarray, t: np.ndarray, w: np.ndarray,
                  grid: np.ndarray) -> np.ndarray:
    """Dealer $gamma per 1% move ($bn) at every grid level, sticky strike. w = dealer contracts
    (+ long). Same scaling as greeks/exposure_profile.py."""
    from trading_intel.greeks.black_scholes import bs_gamma
    out = np.zeros(len(grid))
    for i in range(0, len(grid), 150):
        S = grid[i:i + 150][:, None]
        g = bs_gamma(S, K[None, :], sig[None, :], t[None, :])
        out[i:i + 150] = (g * w[None, :] * MULT * S * S * 0.01).sum(axis=1) / 1e9
    return out


# ---------------------------------------------------------------- DB
Key = tuple  # (expiry, strike, cp, am)


class Db:
    def __init__(self) -> None:
        from sqlalchemy import text

        from trading_intel.memory.db import make_session_factory
        from trading_intel.config import get_settings
        self.text = text
        self.s = make_session_factory(get_settings())()
        self._books: dict[str, list[tuple[datetime, str]]] = {}
        self._rows: dict[tuple[datetime, str], dict[Key, dict]] = {}

    def q(self, sql: str, **kw: Any) -> list[dict]:
        return [dict(r._mapping) for r in self.s.execute(self.text(sql), kw)]

    def books(self) -> list[tuple[datetime, str]]:
        """Every stored SPX book: (ts, 'am'|'eod')."""
        if "all" not in self._books:
            rows = self.q("select distinct ts, source from oi_chain_eod where symbol = 'SPX' "
                          "and source in ('convex_am','convex_am_set','convex_eod','convex_eod_set')")
            seen: dict[datetime, str] = {}
            for r in rows:
                seen[r["ts"]] = "am" if str(r["source"]).startswith("convex_am") else "eod"
            self._books["all"] = sorted(seen.items())
        return self._books["all"]

    def days(self) -> list[date]:
        return sorted({ts.date() for ts, _ in self.books()})

    def book_on(self, d: date, kind: str) -> datetime | None:
        c = [ts for ts, k in self.books() if ts.date() == d and k == kind]
        return min(c) if c else None

    def rows(self, ts: datetime, kind: str) -> dict[Key, dict]:
        if (ts, kind) not in self._rows:
            src = ("convex_am", "convex_am_set") if kind == "am" else ("convex_eod", "convex_eod_set")
            rs = self.q("select expiry, strike, cp, oi, iv, source from oi_chain_eod "
                        "where symbol = 'SPX' and ts = :ts and source in (:a, :b) "
                        "and oi > 0 and iv > 0 and dte <= 400", ts=ts, a=src[0], b=src[1])
            out: dict[Key, dict] = {}
            for r in rs:
                am = str(r["source"]).endswith("_set")
                k = (r["expiry"], float(r["strike"]), str(r["cp"]).upper()[0], am)
                out[k] = {"oi": float(r["oi"]), "iv": float(r["iv"])}
            self._rows[(ts, kind)] = out
        return self._rows[(ts, kind)]

    def spot(self, at: datetime) -> float | None:
        r = self.q("select spot from greeks_snapshots where symbol = 'SPX' and source = 'convex' "
                   "and ts <= :t and spot is not null order by ts desc limit 1", t=at)
        return float(r[0]["spot"]) if r else None

    def tape(self, d: date) -> list[dict]:
        return self.q("select id, symbol, ts, expiry, strike, cp, price, size, quote_loc, spread_leg, "
                      "side, leg_group, root from tas_prints where trade_date = :d "
                      "and root in ('SPX','SPXW','SPXO') and expiry > :d", d=d)


def next_day(days: list[date], d: date) -> date | None:
    later = [x for x in days if x > d]
    return later[0] if later else None


def prev_day(days: list[date], d: date) -> date | None:
    earlier = [x for x in days if x < d]
    return earlier[-1] if earlier else None


def total_oi(rows: dict[Key, dict]) -> float:
    return sum(v["oi"] for v in rows.values())


def oi_asof_close(db: Db, x: date) -> tuple[datetime, str] | None:
    """The book whose open interest is as of day x's close: the next session's morning book if
    its OI has already rolled (differs from x's own 17:15 book), else the next session's 17:15."""
    days = db.days()
    nd = next_day(days, x)
    if nd is None:
        return None
    am, eod_next, eod_x = db.book_on(nd, "am"), db.book_on(nd, "eod"), db.book_on(x, "eod")
    if am is not None:
        if eod_x is None:
            return am, "am"
        a, b = total_oi(db.rows(am, "am")), total_oi(db.rows(eod_x, "eod"))
        if b <= 0 or abs(a - b) / b > 0.001:
            return am, "am"
    if eod_next is not None:
        return eod_next, "eod"
    return None


# ---------------------------------------------------------------- methods
def weights_from(rows: dict[Key, dict], w: dict[Key, float]) -> tuple[list[Key], np.ndarray, np.ndarray]:
    keys = [k for k in w if k in rows and w[k] != 0]
    return keys, np.array([rows[k]["iv"] for k in keys]), np.array([w[k] for k in keys])


def naive_w(rows: dict[Key, dict]) -> dict[Key, float]:
    return {k: naive_sign(k[2]) * v["oi"] for k, v in rows.items()}


def eod_chain(db: Db, through: date) -> list[tuple[date, dict[Key, dict]]]:
    """17:15 books in date order up to `through` (OI as of the prior close, IV at that close)."""
    return [(d, db.rows(ts, "eod")) for d in db.days() if d <= through
            for ts in [db.book_on(d, "eod")] if ts is not None]


def doi_methods(db: Db, last_trade: date, base: dict[Key, dict]) -> tuple[dict | None, dict | None, str]:
    """doi_div and doi_ledger weights. Trades on day X: dOI = E(X+1).oi - E(X).oi,
    dIV = E(X).iv - E(X-1).iv, where E(X) = the 17:15 book of X."""
    days = db.days()
    nx = next_day(days, last_trade)
    if nx is None:
        return None, None, "no book after the last trade day"
    chain = eod_chain(db, nx)
    by_day = dict(chain)
    if last_trade not in by_day or nx not in by_day:
        return None, None, "17:15 books missing around the last trade day"
    seq = [d for d, _ in chain]
    monthly = {k for k in base if is_monthly_or_quarterly(k[0])}
    # one-day version
    one = naive_w(base)
    e1, e0 = by_day[nx], by_day[last_trade]
    pd_ = prev_day(seq, last_trade)
    ivp = by_day[pd_] if pd_ in by_day else {}
    for k in monthly:
        oi1 = e1.get(k, {}).get("oi", 0.0)
        oi0 = e0.get(k, {}).get("oi", 0.0)
        div = (e0[k]["iv"] - ivp[k]["iv"]) if (k in e0 and k in ivp) else None
        one[k] = doi_div_weight(k[2], oi0, oi1, div)
    # ledger: start each monthly contract naive at its first sighting, then step day by day
    led = naive_w(base)
    pos: dict[Key, float] = {}
    for i in range(1, len(seq)):
        X = seq[i - 1]
        if X > last_trade:
            break
        eX, eN = by_day[X], by_day[seq[i]]
        eP = by_day[seq[i - 2]] if i >= 2 else {}
        for k in monthly:
            if k not in eX and k not in eN:
                continue
            oi0 = eX.get(k, {}).get("oi", 0.0)
            oi1 = eN.get(k, {}).get("oi", 0.0)
            if k not in pos:
                pos[k] = naive_sign(k[2]) * oi0
            div = (eX[k]["iv"] - eP[k]["iv"]) if (k in eX and k in eP) else None
            pos[k] = ledger_step(pos[k], k[2], oi0, oi1, div)
    for k in monthly:
        if k in pos:
            led[k] = pos[k]
    return one, led, f"{len(monthly)} monthly/quarterly contracts, ledger over {len(seq)} days"


def tape_methods(db: Db, d: date, start: dict[Key, dict], fresh: dict[Key, dict]
                 ) -> tuple[dict | None, dict | None, str]:
    rows = db.tape(d)
    if not rows:
        return None, None, f"no SPX index tape stored for {d}"
    sys.path.insert(0, str(HERE / "cvxmap"))
    import sides_feed as sf  # noqa: PLC0415
    rows = sf.to_et(rows, sf.tape_clock(rows))
    by_sym: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_sym[r["symbol"]].append(r)
    pkg = sf.sign_packages(r for r in rows if r.get("spread_leg") or r.get("leg_group"))
    cust: dict[Key, float] = defaultdict(float)
    cust_pkg: dict[Key, float] = defaultdict(float)
    for ps in by_sym.values():
        ps.sort(key=lambda r: (r["ts"], r.get("id") or 0))
        for r, (side, how) in zip(ps, sf.classify(ps), strict=True):
            n = int(r.get("size") or 0)
            if n <= 0 or r.get("strike") is None or r.get("cp") not in ("C", "P"):
                continue
            k = (r["expiry"], float(r["strike"]), r["cp"], str(r.get("root") or "") in ("SPX", "SPXO"))
            if how == "leg":
                v = pkg.get(r.get("id"), 0)
                if v:
                    cust_pkg[k] += v * n
                continue
            if side:
                cust[k] += side * n
                cust_pkg[k] += side * n
    out = []
    for c in (cust, cust_pkg):
        w = naive_w(start)
        for k, n in c.items():
            if k in start or k in fresh:
                w[k] = w.get(k, 0.0) - n          # customer buys = dealer short
        out.append(w)
    return out[0], out[1], f"{len(rows)} prints, {len(pkg)} package legs signed"


# ---------------------------------------------------------------- score one reference
def score_ref(db: Db, ref: dict) -> dict[str, Any]:
    d = date.fromisoformat(ref["date"])
    hh, mm = (int(x) for x in ref.get("time", "16:00").split(":"))
    ref_dt = datetime.combine(d, time(hh, mm))
    days = db.days()
    res: dict[str, Any] = {"id": ref["id"], "kind": ref["kind"], "quality": ref.get("quality"),
                           "date": ref["date"], "book": ref["book"], "methods": {}, "notes": []}
    if d not in days and ref["book"] == "close":
        res["notes"].append("no stored book for that day (pruned after 90 days or never collected)")
        return res
    spot = ref.get("spot") or db.spot(ref_dt)
    if spot is None:
        res["notes"].append("no spot")
        return res
    res["spot"] = spot
    books: dict[str, dict[Key, dict]] = {}
    if ref["book"] == "close":
        pdd = prev_day(days, d)
        st = db.book_on(d, "eod") or db.book_on(d, "am")
        if st is not None:
            books["naive_stale"] = db.rows(st, "eod" if db.book_on(d, "eod") else "am")
        fr = oi_asof_close(db, d)
        last_trade = d
        start = books.get("naive_stale")
    else:
        pdd = prev_day(days, d)
        fr = oi_asof_close(db, pdd) if pdd else None
        last_trade = pdd
        start = None
    if fr is None:
        res["notes"].append("no book carrying open interest as of the right close")
        return res
    fresh = db.rows(*fr)
    res["book_used"] = f"{fr[0]:%Y-%m-%d %H:%M} {fr[1]}"
    weights: dict[str, dict[Key, float] | None] = {"naive": naive_w(fresh)}
    if "naive_stale" in books:
        weights["naive_stale"] = naive_w(books["naive_stale"])
    if last_trade is not None:
        one, led, note = doi_methods(db, last_trade, fresh)
        weights["doi_div"], weights["doi_ledger"] = one, led
        res["notes"].append("doi: " + note)
    if ref["book"] == "close" and start is not None:
        t1, t2, note = tape_methods(db, d, start, fresh)
        weights["tape"], weights["tape_pkg"] = t1, t2
        res["notes"].append("tape: " + note)
    if d < date(2026, 11, 10):
        res["notes"].append("before 11/10 the stored book kept only one of the AM monthly / PM weekly "
                            "series on 3rd-Friday dates (key collision fixed 11/10)")

    # time left + the expiry to drop for ex-0DTE
    nxt_exp = next_day(days, d) if ref["book"] == "close" else d
    grid = np.arange(spot * (1 - SPAN), spot * (1 + SPAN) + STEP, STEP)
    for m, w in weights.items():
        if w is None:
            res["methods"][m] = None
            continue
        stale = books.get("naive_stale", {})
        rows = {**fresh, **stale} if m == "naive_stale" else {**stale, **fresh}
        keys, sig, wv = weights_from(rows, w)
        exp = [expiry_dt(k[0], k[3]) for k in keys]
        keep = np.array([e > ref_dt for e in exp], dtype=bool)
        if ref.get("ex0dte"):
            keep &= np.array([k[0] != nxt_exp for k in keys], dtype=bool)
        if not keep.any():
            res["methods"][m] = None
            continue
        K = np.array([k[1] for k in keys])[keep]
        t = np.array([years_left(e, ref_dt) for e in exp])[keep]
        tot = gamma_profile(K, sig[keep], t, wv[keep], grid)
        cross = crossings(grid, tot)
        at_spot = float(np.interp(spot, grid, tot))
        r: dict[str, Any] = {"gamma_at_spot_bn": round(at_spot, 3),
                             "crossings": [[round(x, 1), dd] for x, dd in cross if abs(x - spot) <= spot * 0.05]}
        if ref["kind"] == "flip":
            ours = nearest(cross, float(ref["level"]), ref["dir"])
            r["ours"] = None if ours is None else round(ours, 1)
            r["err"] = None if ours is None else round(abs(ours - float(ref["level"])), 1)
        elif ref["kind"] == "sign":
            r["ok"] = sign_ok(ref["sign"], at_spot)
            if ref.get("value"):
                r["scale_x"] = round(at_spot / float(ref["value"]), 1) if ref["value"] else None
        elif ref["kind"] == "no_cross":
            r["ok"] = not any(ref["lo"] <= x <= ref["hi"] for x, _ in cross)
        res["methods"][m] = r
    return res


# ---------------------------------------------------------------- summary
def summarize(results: list[dict], refs: list[dict]) -> dict[str, Any]:
    by_id = {r["id"]: r for r in refs}
    summ: dict[str, Any] = {}
    for m in METHODS:
        flips_exact, flips_all, signs, scale = [], [], [], []
        for res in results:
            r = (res.get("methods") or {}).get(m)
            ref = by_id[res["id"]]
            if r is None:
                continue
            if ref["kind"] == "flip" and res.get("quality") in COUNTED:
                flips_all.append(r.get("err"))
                if res.get("quality") == "exact":
                    flips_exact.append(r.get("err"))
            elif ref["kind"] in ("sign", "no_cross"):
                signs.append(bool(r.get("ok")))
                if r.get("scale_x") is not None:
                    scale.append(r["scale_x"])
        summ[m] = {
            "flip_exact": verdict(flips_exact), "flip_all": verdict(flips_all),
            "sign_agree": f"{sum(signs)}/{len(signs)}" if signs else None,
            "scale_median_x": None if not scale else round(float(np.median(scale)), 1),
        }
    return summ


def plain_text(results: list[dict], summ: dict[str, Any], run_day: str) -> str:
    L = [f"VS flip score - {run_day}",
         "Question: which way of building our SPX dealer book puts the gamma flip within 10 points of",
         "VolSignals'? Pass = within 10 pts on 70%+ of his flip levels. 'exact' = numbers he printed;",
         "'all' adds levels read off his charts. Sign = does our book agree he was long/short gamma at spot.",
         "Scale = our gamma at spot / his (1.0 = same size).", ""]
    for m in METHODS:
        s = summ[m]
        fe, fa = s["flip_exact"], s["flip_all"]
        if not fa["n"] and not s["sign_agree"]:
            L.append(f"{m:12s} no data (see notes)")
            continue
        L.append(f"{m:12s} flips exact {fe['within_10']}/{fe['n']} (median err {fe['median_err']}) | "
                 f"all {fa['within_10']}/{fa['n']} (median {fa['median_err']}) -> "
                 f"{'PASS' if fa['pass'] else 'not yet'} | sign {s['sign_agree']} | scale x{s['scale_median_x']}")
    L.append("")
    for res in results:
        if res.get("kind") != "flip":
            continue
        parts = []
        for m in METHODS:
            r = (res.get("methods") or {}).get(m)
            if r:
                parts.append(f"{m} {r.get('ours')} ({r.get('err')})")
        L.append(f"{res['id']} [{res.get('quality')}]: " + ("; ".join(parts) if parts else "no data"))
    best = max(METHODS, key=lambda m: (summ[m]["flip_all"]["share"] or 0, -(summ[m]["flip_all"]["median_err"] or 1e9)))
    L += ["", f"Best so far: {best}. Sample is small - one exact flip from him so far; every new flip "
          "he posts gets added to scripts/vs_flip_refs.json and scored the same way.",
          "Positioning read, not a trade signal."]
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refs", default=str(REFS))
    ap.add_argument("--only", help="score one reference id and print its detail")
    ap.add_argument("--telegram", action="store_true")
    ap.add_argument("--out", default=str(OUT_DIR))
    a = ap.parse_args()
    refs = json.loads(Path(a.refs).read_text(encoding="utf-8"))["refs"]
    if a.only:
        refs = [r for r in refs if r["id"] == a.only]
    db = Db()
    results = []
    for ref in refs:
        try:
            results.append(score_ref(db, ref))
        except Exception as exc:  # noqa: BLE001 - one bad reference never stops the run
            results.append({"id": ref["id"], "kind": ref["kind"], "quality": ref.get("quality"),
                            "methods": {}, "notes": [f"error: {exc}"]})
        if a.only:
            print(json.dumps(results[-1], default=str))
        else:
            print(f"scored {ref['id']}")
    summ = summarize(results, refs)
    run_day = datetime.now().strftime("%Y%m%d")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"vs_flip_score_{run_day}.json").write_text(
        json.dumps({"run": run_day, "summary": summ, "results": results}, default=str, indent=1),
        encoding="utf-8")
    txt = plain_text(results, summ, run_day)
    (out / f"vs_flip_score_{run_day}.txt").write_text(txt, encoding="utf-8")
    print(txt)
    if a.telegram:
        try:
            from trading_intel.clients.telegram import TelegramClient
            from trading_intel.config import Settings
            tc = TelegramClient(Settings())
            if tc.enabled:
                tc.send_message("<pre>" + txt.replace("&", "&amp;").replace("<", "&lt;") + "</pre>")
        except Exception as exc:  # noqa: BLE001
            print(f"telegram failed: {exc}")


if __name__ == "__main__":
    main()
