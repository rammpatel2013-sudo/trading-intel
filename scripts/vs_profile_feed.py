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


def book_arrays(rows: dict, ref_dt: datetime, drop_exp: date | None) -> tuple[np.ndarray, ...]:
    """(K, iv, years left, dealer contracts) for every contract still alive at ref_dt; drop_exp
    removes that expiry date (ex-0DTE). rows = {(expiry, strike, cp, am): {oi, iv}}."""
    from vs_flip_score import expiry_dt, naive_sign, years_left

    K, sig, t, w = [], [], [], []
    for (exp, k, cp, am), v in rows.items():
        if drop_exp is not None and exp == drop_exp:
            continue
        e = expiry_dt(exp, am)
        if e <= ref_dt or not (v.get("iv") or 0) > 0 or not (v.get("oi") or 0) > 0:
            continue
        K.append(float(k))
        sig.append(float(v["iv"]))
        t.append(years_left(e, ref_dt))
        w.append(naive_sign(cp) * float(v["oi"]))
    return np.array(K), np.array(sig), np.array(t), np.array(w)


def profile_lines(rows: dict, spot: float, ref_dt: datetime, grid: np.ndarray) -> dict[str, np.ndarray]:
    """Full and ex-0DTE gamma profiles ($bn per 1%) on the grid."""
    from vs_flip_score import gamma_profile

    out = {}
    for name, drop in (("full", None), ("ex0", ref_dt.date())):
        K, sig, t, w = book_arrays(rows, ref_dt, drop)
        out[name] = gamma_profile(K, sig, t, w, grid) if len(K) else np.zeros(len(grid))
    return out


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
    hist = upsert(hist, {"date": ref.date().isoformat(), "spot": round(spot, 2), "full": round(at["full"], 4),
                         "ex0": round(at["ex0"], 4), "flip_full": fl["full"]["nearest"],
                         "flip_ex0": fl["ex0"]["nearest"], "book": f"{ts:%Y-%m-%d %H:%M} {kind}"})
    HIST.write_text(json.dumps(hist, separators=(",", ":")), encoding="utf-8")
    exp0 = ref.date()
    n0 = sum(1 for k in rows if k[0] == exp0)
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
        "hist_n": len(past), "flips": fl, "units": SCALE_NOTE,
        "plain": ("Above zero = dealers long gamma at that price (they sell rallies and buy dips, moves get "
                  "absorbed); below zero = short gamma (they chase, moves speed up). The flip is where a line "
                  "crosses zero. Ex-0DTE = the same book without the options that expire today - the shape "
                  "that is left once they are gone."),
    }


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
