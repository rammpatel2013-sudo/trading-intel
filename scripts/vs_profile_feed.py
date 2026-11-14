#!/usr/bin/env python
# ruff: noqa: E501, N806, RUF100
"""vs_profile_feed.py - created 2026-11-13 for Mithil (VS analysis plan, step 2).

The SPX gamma PROFILE the way VolSignals shows it: dealer gamma (naive book: calls dealer-long,
puts dealer-short, every stored expiry) re-priced at every SPX level, as two lines -
  full    = the whole book,
  ex0     = the same book without the options that expire on the session being read (ex-0DTE),
plus where each line crosses zero (the flip) and, because our $ size is ~29x VolSignals' (different
book), the PERCENTILE of today's gamma at price against our own past sessions.

Book = the newest stored SPX book (08:35 morning pull or 17:15 close pull; open interest as of the
prior close). Reference time = 09:30 of the session being read (the next session after the close),
or now during the session. Sticky strike, each contract's own IV (same maths as vs_flip_score.py).

Writes scripts/cvxmap/data/profile_SPX.json (read by the TEST cockpit's Map > Profile button) and
keeps one row per session in scripts/cvxmap/data/profile_hist_SPX.json (gamma at price, flips) for
the percentile. The first run back-fills that history from the stored books (~90 days, gamma at
price only - a few seconds).

Read-only on the DB, no vendor calls. Run on the NAS (DSM, Mon-Fri 08:45 and 17:35):
    sudo bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_script.sh vs_profile_feed
Descriptor, not a signal (rule 4).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
for p in (str(REPO), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

DATA = HERE / "cvxmap" / "data"
OUT = DATA / "profile_SPX.json"
HIST = DATA / "profile_hist_SPX.json"
SPAN = 0.04          # grid: spot +/- 4%
STEP = 1.0           # 1 SPX point
OPEN = time(9, 30)
CLOSE = time(16, 0)
_TRAPZ = getattr(np, "trapezoid", None) or np.trapz   # numpy < 2.0 on older boxes
MOVES = (-2.0, -1.0, -0.5, 0.5, 1.0, 2.0)   # VS hedge-table moves, % (step 3)
ES_PER_INDEX = 50.0                           # 1 ES = $50 x index; dealer delta in index units / 50 = ES
SCALE_NOTE = ("$bn of dealer gamma per 1% move on OUR naive book - about 29x the size VolSignals "
              "prints, so read the percentile, not the dollar size")


# ---------------------------------------------------------------- pure logic (tests/test_vs_profile_feed.py)
def ref_session(now: datetime, is_session) -> datetime:
    """The session the profile describes: today 09:30 before the open, now during the session,
    otherwise the next session's 09:30."""
    d = now.date()
    if is_session(d) and now.time() < OPEN:
        return datetime.combine(d, OPEN)
    if is_session(d) and now.time() < CLOSE:
        return now.replace(second=0, microsecond=0)
    nd = d + timedelta(days=1)
    while not is_session(nd):
        nd += timedelta(days=1)
    return datetime.combine(nd, OPEN)


def book_arrays(rows: dict, ref_dt: datetime, drop_exp: date | None,
                weights: dict | None = None) -> tuple[np.ndarray, ...]:
    """(K, iv, years left, dealer contracts) for every contract still alive at ref_dt; drop_exp
    removes that expiry date (ex-0DTE). rows = {(expiry, strike, cp, am): {oi, iv}}. weights =
    dealer contracts per key (VS step 4 signed book); default = naive (calls +OI, puts -OI)."""
    from vs_flip_score import expiry_dt, naive_sign, years_left

    K, sig, t, w = [], [], [], []
    keys = rows.keys() if weights is None else weights.keys()
    for key in keys:
        exp, k, cp, am = key
        v = rows.get(key) or {}
        if drop_exp is not None and exp == drop_exp:
            continue
        e = expiry_dt(exp, am)
        if e <= ref_dt or not (v.get("iv") or 0) > 0:
            continue
        wi = naive_sign(cp) * float(v.get("oi") or 0.0) if weights is None else float(weights[key])
        if wi == 0:
            continue
        K.append(float(k))
        sig.append(float(v["iv"]))
        t.append(years_left(e, ref_dt))
        w.append(wi)
    return np.array(K), np.array(sig), np.array(t), np.array(w)


def profile_lines(rows: dict, spot: float, ref_dt: datetime, grid: np.ndarray) -> dict[str, np.ndarray]:
    """Full and ex-0DTE gamma profiles ($bn per 1%) on the grid."""
    from vs_flip_score import gamma_profile

    out = {}
    for name, drop in (("full", None), ("ex0", ref_dt.date())):
        K, sig, t, w = book_arrays(rows, ref_dt, drop)
        out[name] = gamma_profile(K, sig, t, w, grid) if len(K) else np.zeros(len(grid))
    return out


def signed_line(rows: dict, weights: dict, ref_dt: datetime, grid: np.ndarray) -> np.ndarray:
    """VS step 4: the whole book with dealer positions = the start-of-day book (naive) plus the
    last session's prints signed by the tape (customer buy = dealer short), $bn per 1%."""
    from vs_flip_score import gamma_profile

    K, sig, t, w = book_arrays(rows, ref_dt, None, weights)
    return gamma_profile(K, sig, t, w, grid) if len(K) else np.zeros(len(grid))


def last_trade_day(ref_dt: datetime, now: datetime, is_session) -> date:
    """The session whose prints move the book being read: today after the close, else the session
    before the one being read."""
    if ref_dt.date() > now.date() and is_session(now.date()) and now.time() >= CLOSE:
        return now.date()
    d = ref_dt.date() - timedelta(days=1)
    while not is_session(d):
        d -= timedelta(days=1)
    return d


def flips(grid: np.ndarray, tot: np.ndarray, spot: float) -> dict[str, Any]:
    """Zero crossings, plus the nearest one below and above spot."""
    from vs_flip_score import crossings

    cr = crossings(grid, tot)
    lv = [x for x, _ in cr]
    below = max((x for x in lv if x <= spot), default=None)
    above = min((x for x in lv if x > spot), default=None)
    near = min(lv, key=lambda x: abs(x - spot)) if lv else None
    r = lambda x: None if x is None else round(x, 1)  # noqa: E731
    return {"all": [[round(x, 1), d] for x, d in cr], "below": r(below), "above": r(above), "nearest": r(near)}


def hedge_rows(grid: np.ndarray, tot_bn: np.ndarray, spot: float,
               moves: tuple[float, ...] = MOVES) -> list[dict[str, Any]]:
    """VS step 3 - ES contracts dealers must trade to stay hedged if SPX moves from spot by each %.
    The gamma line ($bn per 1%) is turned back into dealer gamma in index units per point,
    Gamma(S) = G$ * 1e9 / (0.01 * S^2), and integrated from spot to the new level (trapezoid on the
    1-pt grid): the dealer delta change. Dealers trade the opposite of it: + = buy ES, - = sell."""
    gam = tot_bn * 1e9 / (0.01 * grid * grid)
    out = []
    for m in moves:
        s1 = spot * (1 + m / 100.0)
        if not (grid[0] <= min(spot, s1) and max(spot, s1) <= grid[-1]):
            continue
        lo, hi = min(spot, s1), max(spot, s1)
        xs = np.concatenate(([lo], grid[(grid > lo) & (grid < hi)], [hi]))
        ys = np.interp(xs, grid, gam)
        d_delta = float(_TRAPZ(ys, xs)) * (1 if s1 >= spot else -1)
        out.append({"move_pct": m, "spx": round(s1, 1), "es": int(round(-d_delta / ES_PER_INDEX))})
    return out


def charm_bn(K, sig, t, w, grid) -> np.ndarray:
    """Dealer charm on the grid: $bn of dealer delta that time alone adds per DAY (+ = dealers will
    have delta to SELL, - = to BUY), sticky strike. bs_charm = dDelta/dt per year (calls = puts)."""
    from trading_intel.greeks.black_scholes import bs_charm

    out = np.zeros(len(grid))
    if not len(K):
        return out
    for i in range(0, len(grid), 150):
        S = grid[i:i + 150][:, None]
        c = bs_charm(S, K[None, :], sig[None, :], t[None, :])
        out[i:i + 150] = (c * w[None, :] * 100.0 * S).sum(axis=1) / 365.0 / 1e9
    return out


def expiry_buckets(rows: dict, ref_dt: datetime) -> list[tuple[str, set]]:
    """VS step 5 tenors: today (0DTE), the next expiry, the rest of 2-7 days, then 8+ days."""
    exps = sorted({k[0] for k in rows if k[0] >= ref_dt.date()})
    d0 = ref_dt.date()
    today = {e for e in exps if e == d0}
    later = [e for e in exps if e > d0]
    nxt = set(later[:1])
    wk = {e for e in later[1:] if (e - d0).days <= 7}
    far = {e for e in later if (e - d0).days > 7} - nxt
    out = [("0DTE", today), (f"next ({later[0]:%a %m/%d})" if later else "next", nxt), ("2-7 days", wk), ("8+ days", far)]
    return [(n, e) for n, e in out if e]


def charm_view(rows: dict, ref_dt: datetime, grid: np.ndarray, spot: float) -> dict[str, Any]:
    """VS step 5 - charm by tenor across level, charm at price through the session, and the red flag
    (mixed-sign charm within +/- half a 0DTE straddle of price)."""
    lines = {}
    tot = np.zeros(len(grid))
    for name, exps in expiry_buckets(rows, ref_dt):
        sub = {k: v for k, v in rows.items() if k[0] in exps}
        K, sig, t, w = book_arrays(sub, ref_dt, None)
        ln = charm_bn(K, sig, t, w, grid)
        lines[name] = [round(float(x), 4) for x in ln]
        tot += ln
    # charm at price through the session (frozen spot), whole book
    clock = []
    t0 = ref_dt
    # to 15:00 only: inside the last hour the 0DTE time left hits the 1-hour floor (MIN_T_S) and charm would freeze
    for hh, mm in ((9, 30), (10, 0), (10, 30), (11, 0), (11, 30), (12, 30), (13, 30), (14, 30), (15, 0)):
        at = datetime.combine(ref_dt.date(), time(hh, mm))
        if at < t0.replace(second=0, microsecond=0):
            continue
        K, sig, t, w = book_arrays(rows, at, None)
        clock.append({"t": f"{hh:02d}:{mm:02d}", "bn": round(float(charm_bn(K, sig, t, w, np.array([spot]))[0]), 4)})
    # 0DTE straddle ~ 0.8 * S * iv * sqrt(time left) at the ATM strike of today's expiry
    d0 = [(abs(k[1] - spot), v["iv"]) for k, v in rows.items() if k[0] == ref_dt.date() and (v.get("iv") or 0) > 0]
    tl = max((datetime.combine(ref_dt.date(), CLOSE) - ref_dt).total_seconds(), 3600.0) / (365.0 * 86400.0)
    strad = 0.8 * spot * min(d0)[1] * tl ** 0.5 if d0 else None
    return {"lines": lines, "total": [round(float(x), 4) for x in tot], "clock": clock,
            "straddle": None if strad is None else round(strad, 1), "red_flag": red_flag(grid, tot, spot, strad),
            "units": "$bn of dealer delta per day from time alone; + = dealers will SELL that delta, - = BUY"}


def red_flag(grid: np.ndarray, tot: np.ndarray, spot: float, straddle: float | None) -> dict[str, Any]:
    """Mixed-sign charm near price (VolSignals' red flag): inside spot +/- half a straddle, count
    sign changes and the smaller side's share of the area. Flag = >= 1 change and >= 25% share."""
    half = (straddle or spot * 0.004) / 2.0
    m = (grid >= spot - half) & (grid <= spot + half)
    v = tot[m]
    if len(v) < 3:
        return {"flag": False, "changes": 0, "minor_share": 0.0, "band": [None, None]}
    ch = int(np.sum(np.signbit(v[1:]) != np.signbit(v[:-1])))
    pos, neg = float(v[v > 0].sum()), float(-v[v < 0].sum())
    share = min(pos, neg) / max(pos, neg) if max(pos, neg) > 0 else 0.0
    return {"flag": bool(ch >= 1 and share >= 0.25), "changes": ch, "minor_share": round(share, 2),
            "band": [round(spot - half, 1), round(spot + half, 1)]}


IN_STRAD, OUT_2STRAD = 0.5763, 0.1096   # normal: P(|z| <= 0.8), P(|z| > 1.6) - an ATM straddle ~ 0.8 sigma


def scaled_moves(days: list, close: dict, vix: dict) -> list[tuple[Any, float]]:
    """VS step 6 check 1: each session's close-to-close move in units of the move the options priced
    the evening before (VIX / 100 / sqrt(252)); z = 0.8 is one straddle. Days without both closes skip."""
    out = []
    for a, b in zip(days, days[1:]):
        if a in close and b in close and a in vix and vix[a] and close[a] > 0:
            sd = vix[a] / 100.0 / 252 ** 0.5
            out.append((b, float(np.log(close[b] / close[a]) / sd)))
    return out


def calib(z: list[float]) -> dict[str, Any] | None:
    """Realized vs priced: share inside one straddle, share beyond two, mean |z| (normal = 0.80)."""
    if len(z) < 20:
        return None
    a = np.abs(np.array(z))
    inside, beyond = float(np.mean(a <= 0.8)), float(np.mean(a > 1.6))
    return {"n": len(z), "inside_pct": round(100 * inside, 1), "inside_priced_pct": round(100 * IN_STRAD, 1),
            "beyond2_pct": round(100 * beyond, 1), "beyond2_priced_pct": round(100 * OUT_2STRAD, 1),
            "mean_abs_z": round(float(a.mean()), 2)}


def by_gamma(zd: list[tuple[Any, float]], gex_prior: dict) -> dict[str, Any]:
    """VS step 6 check 2: the same, split by naive dealer gamma at the prior close - long, short
    (shallow half), short (deep half: the most negative half of the short days)."""
    rows = [(gex_prior[d], z) for d, z in zd if gex_prior.get(d) is not None]
    neg = sorted(g for g, _ in rows if g < 0)
    cut = neg[len(neg) // 2] if neg else 0.0
    groups = {"long": [z for g, z in rows if g > 0],
              "short_shallow": [z for g, z in rows if cut < g < 0 or (g < 0 and len(neg) == 1)],
              "short_deep": [z for g, z in rows if g <= cut and g < 0 and len(neg) > 1]}
    out = {}
    for k, v in groups.items():
        a = np.abs(np.array(v)) if v else np.array([])
        out[k] = {"n": len(v), "inside_pct": round(100 * float(np.mean(a <= 0.8)), 1) if len(v) else None,
                  "mean_abs_z": round(float(a.mean()), 2) if len(v) else None}
    return out


def percentile(v: float, hist: list[float]) -> int | None:
    """Share of past values at or below v, 0-100 (None with fewer than 10 past sessions)."""
    h = [x for x in hist if x is not None]
    if len(h) < 10 or v is None:
        return None
    return int(round(100.0 * sum(1 for x in h if x <= v) / len(h)))


def upsert(hist: list[dict], row: dict) -> list[dict]:
    """One row per session date (re-runs replace)."""
    out = [h for h in hist if h.get("date") != row["date"]] + [row]
    return sorted(out, key=lambda h: h["date"])


# ---------------------------------------------------------------- DB side
def newest_book(db, now: datetime) -> tuple[datetime, str] | None:
    c = [(ts, k) for ts, k in db.books() if ts <= now]
    return c[-1] if c else None


def backfill(db, hist: list[dict], days: int = 90) -> list[dict]:
    """Gamma at price for every stored session (book of that morning, else that close), so the
    percentile has a history from day one. Cheap: one price point per day."""
    from trading_intel.timeutils import is_trading_session

    have = {h["date"] for h in hist}
    for d in db.days()[-days:]:
        if d.isoformat() in have or not is_trading_session(d):
            continue
        ts = db.book_on(d, "am")
        kind = "am"
        if ts is None:
            ts, kind = db.book_on(d, "eod"), "eod"
        if ts is None:
            continue
        ref = datetime.combine(d, OPEN)
        spot = db.spot(datetime.combine(d, time(9, 35))) or db.spot(ts)
        if not spot:
            continue
        rows = db.rows(ts, kind)
        g = np.array([spot])
        L = profile_lines(rows, spot, ref, g)
        hist = upsert(hist, {"date": d.isoformat(), "spot": round(spot, 2), "full": round(float(L["full"][0]), 4),
                             "ex0": round(float(L["ex0"][0]), 4), "book": f"{ts:%Y-%m-%d %H:%M} {kind}",
                             "backfill": True})
    return hist


def build(db, now: datetime) -> dict[str, Any]:
    from trading_intel.timeutils import is_trading_session

    ref = ref_session(now, is_trading_session)
    bk = newest_book(db, now)
    if bk is None:
        return {"ok": False, "reason": "no stored SPX book"}
    ts, kind = bk
    rows = db.rows(ts, kind)
    spot = db.spot(now)
    if not spot:
        return {"ok": False, "reason": "no SPX spot"}
    grid = np.arange(round(spot * (1 - SPAN)), round(spot * (1 + SPAN)) + STEP, STEP)
    L = profile_lines(rows, spot, ref, grid)
    at = {k: float(np.interp(spot, grid, v)) for k, v in L.items()}
    hist = json.loads(HIST.read_text(encoding="utf-8")) if HIST.exists() else []
    if len(hist) < 20:
        hist = backfill(db, hist)
    past = [h for h in hist if h["date"] < ref.date().isoformat()]
    pct = {k: percentile(at[k], [h.get(k) for h in past]) for k in ("full", "ex0")}
    fl = {k: flips(grid, v, spot) for k, v in L.items()}
    charm = charm_view(rows, ref, grid, spot)
    hist = upsert(hist, {"date": ref.date().isoformat(), "spot": round(spot, 2), "full": round(at["full"], 4),
                         "ex0": round(at["ex0"], 4), "flip_full": fl["full"]["nearest"],
                         "flip_ex0": fl["ex0"]["nearest"], "book": f"{ts:%Y-%m-%d %H:%M} {kind}",
                         "red_flag": charm["red_flag"]["flag"], "straddle": charm["straddle"]})
    HIST.write_text(json.dumps(hist, separators=(",", ":")), encoding="utf-8")
    exp0 = ref.date()
    n0 = sum(1 for k in rows if k[0] == exp0)
    signed = signed_view(db, ref, now, rows, grid, spot)
    odds = odds_view(db, now) if hasattr(db, "s") else {}
    return {
        "ok": True, "v": 1, "sym": "SPX", "written": f"{now:%Y-%m-%d %H:%M}",
        "ref": f"{ref:%Y-%m-%d %H:%M}", "session": ref.date().isoformat(),
        "book": {"ts": f"{ts:%Y-%m-%d %H:%M}", "kind": kind, "oi_note": "open interest as of the prior close",
                 "contracts": len(rows), "expiring_today": n0},
        "spot": round(spot, 2), "grid": {"lo": float(grid[0]), "step": STEP, "n": len(grid)},
        "full": [round(float(x), 4) for x in L["full"]], "ex0": [round(float(x), 4) for x in L["ex0"]],
        "at_spot": {k: round(v, 4) for k, v in at.items()}, "pct": pct,
        "hedge": [dict(r, es_ex0=e["es"]) for r, e in zip(hedge_rows(grid, L["full"], spot),
                                                         hedge_rows(grid, L["ex0"], spot), strict=True)],
        "hedge_note": ("ES contracts dealers would trade to stay hedged if SPX moved there from now "
                       "(+ buy, - sell), naive book: about 30x VolSignals' size - read which way and "
                       "which side is bigger, not the number"),
        "hist_n": len(past), "flips": fl, "units": SCALE_NOTE, "signed": signed, "charm": charm, "odds": odds,
        "plain": ("Above zero = dealers long gamma at that price (they sell rallies and buy dips, moves get "
                  "absorbed); below zero = short gamma (they chase, moves speed up). The flip is where a line "
                  "crosses zero. Ex-0DTE = the same book without the options that expire today - the shape "
                  "that is left once they are gone."),
    }


def signed_view(db, ref: datetime, now: datetime, rows: dict, grid: np.ndarray, spot: float) -> dict[str, Any]:
    """VS step 4 view (separate from the naive lines; the ledger stays unseeded, 11/06 rule)."""
    from trading_intel.timeutils import is_trading_session
    from vs_flip_score import tape_methods

    p = last_trade_day(ref, now, is_trading_session)
    st_ts, kind = db.book_on(p, "am"), "am"
    if st_ts is None:
        st_ts, kind = db.book_on(p, "eod"), "eod"
    if st_ts is None:
        return {"ok": False, "day": p.isoformat(), "reason": f"no stored book for {p}"}
    start = db.rows(st_ts, kind)
    fresh = rows if ref.date() > p else {}
    try:
        t1, t2, note = tape_methods(db, p, start, fresh)
    except Exception as exc:  # noqa: BLE001 - the tape is optional; the naive lines stand alone
        return {"ok": False, "day": p.isoformat(), "reason": f"tape read failed: {str(exc)[:120]}"}
    w = t2 or t1
    if not w:
        return {"ok": False, "day": p.isoformat(), "reason": note}
    line = signed_line({**start, **rows}, w, ref, grid)
    return {"ok": True, "day": p.isoformat(), "how": "packages" if t2 else "single prints", "note": note,
            "start_book": f"{st_ts:%Y-%m-%d %H:%M} {kind}", "line": [round(float(x), 4) for x in line],
            "at_spot": round(float(np.interp(spot, grid, line)), 4), "flips": flips(grid, line, spot)}


def odds_view(db, now: datetime) -> dict[str, Any]:
    """VS step 6: get_close_odds (front expiry, cut at the breakevens, the flip and 2x straddle) plus the
    three calibration checks. Each part fails on its own with a plain reason."""
    out: dict[str, Any] = {}
    try:
        from trading_intel.mcp.event_tools import get_close_odds
        out["odds"] = get_close_odds(db.s, "SPX")
    except Exception as exc:  # noqa: BLE001
        db.s.rollback()
        out["odds"] = {"found": False, "reason": str(exc)[:160]}
    # check 1 + 2: SPX closes, VIX closes, gamma at the prior close - all from our own DB
    try:
        from trading_intel.mcp import tools as _t
        gh = _t.get_gamma_history(db.s, "SPX", days=365)
        qs = db.q("select date, close from quotes_daily where symbol = 'SPX' and date >= :d order by date",
                  d=(now - timedelta(days=740)).date())
        close = {r["date"].isoformat(): float(r["close"]) for r in qs if r["close"]}
        vix_all: dict[str, float] = {}
        vr = db.q("select date, vix from vix_data where vix is not null and date >= :d", d=(now - timedelta(days=740)).date())
        vix_all.update({r["date"].isoformat(): float(r["vix"]) for r in vr})
        days = sorted(close)
        zd = scaled_moves(days, close, vix_all)
        out["calib"] = calib([z for _, z in zd])
        gx = {r["date"]: r.get("gex_total") for r in gh.get("rows", [])}
        prior = {b: gx.get(a) for a, b in zip(days, days[1:])}
        out["by_gamma"] = by_gamma(zd, prior)
        out["calib_span"] = [days[0], days[-1]] if days else None
    except Exception as exc:  # noqa: BLE001
        db.s.rollback()
        out["calib_err"] = str(exc)[:160]
    try:
        from trading_intel.clients.fred import FredClient
        from trading_intel.config import get_settings
        from trading_intel.mcp.event_tools import get_event_day_stats
        ev = get_event_day_stats(db.s, fred=FredClient(get_settings()))
        out["events"] = {"event": ev.get("event"), "summary": ev.get("summary")} if ev.get("found") else {"reason": ev.get("reason")}
    except Exception as exc:  # noqa: BLE001
        db.s.rollback()
        out["events"] = {"reason": str(exc)[:160]}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--now", help="YYYY-MM-DDTHH:MM (Eastern) - rebuild as of then")
    a = ap.parse_args()
    from trading_intel.timeutils import eastern_now
    from vs_flip_score import Db

    now = datetime.fromisoformat(a.now) if a.now else eastern_now().replace(tzinfo=None)
    out = build(Db(), now)
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    tmp.replace(OUT)
    if out.get("ok"):
        f = out["flips"]
        print(f"vs_profile_feed.done session={out['session']} spot={out['spot']} full={out['at_spot']['full']:+.2f} "
              f"(pct {out['pct']['full']}) flip={f['full']['nearest']} ex0={out['at_spot']['ex0']:+.2f} "
              f"(pct {out['pct']['ex0']}) flip={f['ex0']['nearest']} hist={out['hist_n']}")
    else:
        print("vs_profile_feed.skip", out.get("reason"))


if __name__ == "__main__":
    main()
