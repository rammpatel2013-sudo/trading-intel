#!/usr/bin/env python
# ruff: noqa: E501, RUF100
"""whales_feed.py - 2026-11-13 (overnight run), Claude for Mithil. Backlog #97 step 2: whales vs crowd.

Reads ``tas_crowd_minute`` (every SPX/SPXW tape print, summed per minute x contract x size bucket by
tas_capture_job since ship_20261113_crowd) and writes the Flow tab's "Whales" lines:

  whale = prints of >= TAS_WHALE_MIN_SIZE contracts (20), crowd = smaller prints.
  For each bucket and scope (0DTE = today's expiry, All = every SPX/SPXW expiry):
    customer signed $ delta per minute = sum(side x size x delta x 100) x SPX
    (buy +, sell -; mid / unknown prints are not signed), cumulative from 09:30, in $M.
  "Opposed" 5-minute windows: whales and crowd pushed opposite ways, each by at least
  OPP_MIN_SHARE of the bucket's median |5-min| flow that day (and at least OPP_FLOOR_M $M).

Output: scripts/cvxmap/data/whales_live.json (read by the cockpit Flow tab button "Whales", OFF by
default). After the close it also keeps reports/whales/days/<YYYYMMDD>.json (the opposed windows with
SPX at the window end and 30 minutes later) and reports/whales/score.json: in opposed windows, how
often SPX moved the WHALES' way over the next 30 minutes vs the crowd's way. Verdict only after
MIN_SESSIONS sessions (20) - until then "collecting".

RUN (NAS, DSM task "TI whales feed", Mon-Fri 09:31, user root; exits by itself after 16:05):
    bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_script.sh whales_feed
  One pass now:            ... run_script.sh whales_feed --once
  A stored day:            ... run_script.sh whales_feed --date 2026-11-16
  Re-score all days:       ... run_script.sh whales_feed --score

2026-11-21 (VIX map redesign C3, test cockpit "Strike map"): the same pass also writes
scripts/cvxmap/data/vix_whales_live.json from the VIX / VIXW rows of tas_crowd_minute (summed there since 11/19, every
print, every tape session) - IN CONTRACTS (signed lots = bought - sold), not $ delta:
  * whales vs crowd cumulative from 09:30 (+ the overnight / pre-market total before 09:30 as its own number);
  * opposed 5-minute stretches (each side >= OPP_MIN_SHARE of its median |5-min| lots and >= VIX_OPP_FLOOR lots);
  * per-minute bought / sold / total contracts (the map's delta volume bars) and VIX each minute;
  * a TAPE box from the last VIX_TAPE_MIN minutes: who is winning (bought vs sold share), lots per minute, whale share,
    which way customers pushed VIX exposure (signed delta), ABSORPTION when that push met a VIX move the other way,
    and "heavy volume" = the last full minute vs the median of the same minute over the previous VIX_NORM_DAYS
    sessions (today's median minute until 3 sessions are stored).
Display only, no score (yet).

Read-only on the DB, no vendor calls (rule 1), no AI (rule 7). Descriptive flow, not a signal (rule 4).
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from collections import defaultdict
from collections.abc import Callable
from datetime import date, datetime
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

ET = ZoneInfo("America/New_York")
DATA = HERE / "cvxmap" / "data"
OUT = DATA / "whales_live.json"
REP = REPO / "reports" / "whales"
OPEN_M, CLOSE_M = 9 * 60 + 30, 16 * 60
WIN = 5                 # opposed-window length, minutes
FWD = 30                # scoring horizon, minutes
OPP_MIN_SHARE = 0.5     # each side >= 50% of its median |5-min| flow that day
OPP_FLOOR_M = 1.0       # and >= $1M signed delta
MIN_SESSIONS = 20
EVERY_S = 60
START_HM, END_HM = (9, 31), (16, 5)
VIX_OUT = DATA / "vix_whales_live.json"
VIX_ROOTS = ("VIX", "VIXW")
VIX_OPP_FLOOR = 10      # lots: an opposed VIX stretch needs >= 10 contracts each way
VIX_TAPE_MIN = 5        # tape box window, minutes
VIX_NORM_DAYS = 20      # "heavy volume" norm: median of the same minute over the previous 20 sessions


# ---------------------------------------------------------------- pure logic (tests/test_whales_feed.py)
def _mins(dt: datetime) -> int:
    return dt.hour * 60 + dt.minute


def _hm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def minute_flows(rows: list[dict], trade_date: date) -> tuple[dict, dict]:
    """rows = tas_crowd_minute dicts. Returns (flows, spot):
    flows[scope][bucket][minute] = $ signed delta (customer side), scope '0dte' | 'all';
    spot[minute] = last SPX seen that minute."""
    flows: dict = {s: {b: defaultdict(float) for b in ("whale", "crowd")} for s in ("0dte", "all")}
    spot: dict[int, float] = {}
    for r in rows:
        m = _mins(r["minute"])
        if m < OPEN_M or m > CLOSE_M:
            continue
        sp = r.get("spot")
        if sp:
            spot[m] = float(sp)
    last = None
    for m in range(OPEN_M, CLOSE_M + 1):        # carry spot forward through quiet minutes
        if m in spot:
            last = spot[m]
        elif last is not None:
            spot[m] = last
    for r in rows:
        m = _mins(r["minute"])
        if m < OPEN_M or m > CLOSE_M or r.get("bucket") not in ("whale", "crowd"):
            continue
        sp = spot.get(m) or r.get("spot")
        if not sp:
            continue
        usd = float(r.get("signed_delta") or 0.0) * float(sp)
        flows["all"][r["bucket"]][m] += usd
        if r.get("expiry") == trade_date:
            flows["0dte"][r["bucket"]][m] += usd
    return flows, spot


def cumulative(per_min: dict, last_m: int) -> list[float]:
    """Running total in $M from 09:30 to last_m (inclusive)."""
    out, run = [], 0.0
    for m in range(OPEN_M, last_m + 1):
        run += per_min.get(m, 0.0)
        out.append(round(run / 1e6, 3))
    return out


def opposed(whale: dict, crowd: dict, last_m: int) -> list[dict]:
    """5-minute windows where whales and crowd pushed opposite ways, each by a meaningful amount."""
    wins = []
    for t0 in range(OPEN_M, last_m - WIN + 2, WIN):
        w = sum(whale.get(m, 0.0) for m in range(t0, t0 + WIN))
        c = sum(crowd.get(m, 0.0) for m in range(t0, t0 + WIN))
        wins.append((t0, w, c))
    if not wins:
        return []
    med_w = statistics.median([abs(w) for _, w, _ in wins]) or 0.0
    med_c = statistics.median([abs(c) for _, _, c in wins]) or 0.0
    out = []
    for t0, w, c in wins:
        if w * c >= 0:
            continue
        if abs(w) < max(OPP_MIN_SHARE * med_w, OPP_FLOOR_M * 1e6) or abs(c) < max(OPP_MIN_SHARE * med_c, OPP_FLOOR_M * 1e6):
            continue
        out.append({"t0": _hm(t0), "t1": _hm(t0 + WIN), "whale": round(w / 1e6, 2), "crowd": round(c / 1e6, 2),
                    "whales_side": "up" if w > 0 else "down"})
    return out


def build(rows: list[dict], trade_date: date, now_m: int, whale_min: int) -> dict:
    flows, spot = minute_flows(rows, trade_date)
    have = [m for s in flows.values() for b in s.values() for m in b]
    if not have:
        return {"ok": False, "date": trade_date.isoformat(), "reason": "no SPX/SPXW prints in tas_crowd_minute for this day yet"}
    last_m = max(OPEN_M, min(now_m, CLOSE_M, max(have)))
    out: dict = {"ok": True, "date": trade_date.isoformat(), "asof": _hm(last_m), "whale_min": whale_min,
                 "t0": _hm(OPEN_M), "step_min": 1, "n": last_m - OPEN_M + 1, "series": {}, "opp": {}, "totals": {},
                 "spot": [round(spot[m], 2) if m in spot else None for m in range(OPEN_M, last_m + 1)],
                 "note": "customer signed $ delta (buy +, sell -), cumulative from 09:30, $M; mid/unknown prints unsigned"}
    for scope, b in flows.items():
        out["series"][scope] = {k: cumulative(b[k], last_m) for k in ("whale", "crowd")}
        out["opp"][scope] = opposed(b["whale"], b["crowd"], last_m)
        out["totals"][scope] = {k: out["series"][scope][k][-1] if out["series"][scope][k] else 0.0 for k in ("whale", "crowd")}
    return out


def day_record(live: dict) -> dict:
    """Opposed windows with SPX at the window end and FWD minutes later (for the score)."""
    spot = live.get("spot") or []

    def at(hm: str, plus: int = 0) -> float | None:
        h, m = hm.split(":")
        i = int(h) * 60 + int(m) + plus - OPEN_M
        return spot[i] if 0 <= i < len(spot) else None

    rec: dict = {"date": live["date"], "windows": {}}
    for scope, wins in (live.get("opp") or {}).items():
        rows = []
        for w in wins:
            a, b = at(w["t1"]), at(w["t1"], FWD)
            rows.append({**w, "spx_end": a, "spx_fwd": b})
        rec["windows"][scope] = rows
    return rec


def score(days: list[dict]) -> dict:
    """In opposed windows: share of resolved windows where SPX moved the whales' way over FWD minutes."""
    out: dict = {"sessions": len(days), "min_sessions": MIN_SESSIONS, "horizon_min": FWD, "scopes": {}}
    for scope in ("0dte", "all"):
        n = hit = flat = 0
        for d in days:
            for w in (d.get("windows") or {}).get(scope, []):
                a, b = w.get("spx_end"), w.get("spx_fwd")
                if a is None or b is None:
                    continue
                if abs(b - a) < 0.5:
                    flat += 1
                    continue
                n += 1
                hit += (b > a) == (w["whales_side"] == "up")
        out["scopes"][scope] = {"windows": n, "flat": flat, "whales_right": hit,
                                "whales_right_pct": round(100 * hit / n, 1) if n else None}
    s0 = out["scopes"]["0dte"]
    if len(days) < MIN_SESSIONS or not s0["windows"]:
        out["verdict"] = f"collecting ({len(days)}/{MIN_SESSIONS} sessions)"
    else:
        p = s0["whales_right_pct"]
        out["verdict"] = ("whales lead" if p >= 60 else "crowd leads" if p <= 40 else "no edge either way") + f" (0DTE {p}% of {s0['windows']} windows)"
    return out


# ---------------------------------------------------------------- VIX (2026-11-21, C3): lots, not $ delta
def vix_build(rows: list[dict], trade_date: date, now_m: int, whale_min: int, norm: dict | None = None) -> dict:
    """rows = [{minute, bucket, buy, sell, other, sd, spot}] (one per minute x bucket, any session of trade_date).
    norm = {minute of day: median total contracts of that minute over earlier sessions}. Pure (tests)."""
    norm = norm or {}
    rth = [r for r in rows if OPEN_M <= _mins(r["minute"]) <= CLOSE_M and r.get("bucket") in ("whale", "crowd")]
    pre = [r for r in rows if r["minute"].date() < trade_date or _mins(r["minute"]) < OPEN_M]
    if not rth and not pre:
        return {"ok": False, "date": trade_date.isoformat(), "reason": "no VIX prints in tas_crowd_minute for this day yet"}
    pre_out = {b: round(sum((r["buy"] or 0) - (r["sell"] or 0) for r in pre if r.get("bucket") == b)) for b in ("whale", "crowd")}
    pre_out["tot"] = round(sum((r["buy"] or 0) + (r["sell"] or 0) + (r["other"] or 0) for r in pre))
    if pre:
        pre_out["from"] = min(r["minute"] for r in pre).strftime("%m-%d %H:%M")
    if not rth:
        return {"ok": False, "date": trade_date.isoformat(), "reason": "no VIX prints since 09:30 yet", "pre": pre_out}
    last_m = max(OPEN_M, min(now_m, CLOSE_M, max(_mins(r["minute"]) for r in rth)))
    net: dict = {b: defaultdict(float) for b in ("whale", "crowd")}
    buy, sell, tot, sd, spot = defaultdict(float), defaultdict(float), defaultdict(float), defaultdict(float), {}
    wg: dict = defaultdict(float)                      # whale bought + sold (gross)
    for r in rth:
        m = _mins(r["minute"])
        b, s_, o = float(r["buy"] or 0), float(r["sell"] or 0), float(r["other"] or 0)
        net[r["bucket"]][m] += b - s_
        if r["bucket"] == "whale":
            wg[m] += b + s_
        buy[m] += b
        sell[m] += s_
        tot[m] += b + s_ + o
        sd[m] += float(r.get("sd") or 0)
        if r.get("spot"):
            spot[m] = float(r["spot"])
    sp, lastv = [], None
    for m in range(OPEN_M, last_m + 1):
        lastv = spot.get(m, lastv)
        sp.append(round(lastv, 2) if lastv else None)
    rng = range(OPEN_M, last_m + 1)
    out: dict = {"ok": True, "date": trade_date.isoformat(), "asof": _hm(last_m), "whale_min": whale_min,
                 "t0": _hm(OPEN_M), "n": last_m - OPEN_M + 1, "units": "contracts",
                 "series": {b: [round(x) for x in _cum(net[b], last_m)] for b in ("whale", "crowd")},
                 "vol": {"buy": [round(buy[m]) for m in rng], "sell": [round(sell[m]) for m in rng],
                         "tot": [round(tot[m]) for m in rng]},
                 "spot": sp, "pre": pre_out,
                 "opp": vix_opposed(net["whale"], net["crowd"], last_m),
                 "tape": vix_tape(buy, sell, tot, sd, wg, sp, last_m, norm),
                 "note": "VIX + VIXW option prints, signed lots (bought - sold), cumulative from 09:30; "
                         "mid / unknown prints count in volume, not in the lines"}
    out["totals"] = {b: out["series"][b][-1] if out["series"][b] else 0 for b in ("whale", "crowd")}
    return out


def _cum(per_min: dict, last_m: int) -> list[float]:
    out, run = [], 0.0
    for m in range(OPEN_M, last_m + 1):
        run += per_min.get(m, 0.0)
        out.append(run)
    return out


def vix_opposed(whale: dict, crowd: dict, last_m: int) -> list[dict]:
    """Same rule as opposed() in lots: 5-minute windows where whales and crowd pushed opposite ways."""
    wins = [(t0, sum(whale.get(m, 0.0) for m in range(t0, t0 + WIN)), sum(crowd.get(m, 0.0) for m in range(t0, t0 + WIN)))
            for t0 in range(OPEN_M, last_m - WIN + 2, WIN)]
    if not wins:
        return []
    med_w = statistics.median([abs(w) for _, w, _ in wins]) or 0.0
    med_c = statistics.median([abs(c) for _, _, c in wins]) or 0.0
    return [{"t0": _hm(t0), "t1": _hm(t0 + WIN), "whale": round(w), "crowd": round(c), "whales_side": "up" if w > 0 else "down"}
            for t0, w, c in wins
            if w * c < 0 and abs(w) >= max(OPP_MIN_SHARE * med_w, VIX_OPP_FLOOR) and abs(c) >= max(OPP_MIN_SHARE * med_c, VIX_OPP_FLOOR)]


def vix_tape(buy: dict, sell: dict, tot: dict, sd: dict, wgross: dict, sp: list, last_m: int, norm: dict) -> dict:
    """The tape box (last VIX_TAPE_MIN minutes)."""
    ms = range(max(OPEN_M, last_m - VIX_TAPE_MIN + 1), last_m + 1)
    b, s_ = sum(buy.get(m, 0.0) for m in ms), sum(sell.get(m, 0.0) for m in ms)
    t = sum(tot.get(m, 0.0) for m in ms)
    share = (b - s_) / (b + s_) if b + s_ else 0.0
    a = abs(share)
    strength = "strong" if a >= 0.4 else "moderate" if a >= 0.15 else "balanced"
    who = "BUYERS" if share > 0 and a >= 0.15 else "SELLERS" if share < 0 and a >= 0.15 else "NOBODY"
    push = sum(sd.get(m, 0.0) for m in ms)                  # customer VIX delta (contracts x delta x 100)
    i0, i1 = ms.start - OPEN_M - 1, last_m - OPEN_M
    v0 = sp[i0] if 0 <= i0 < len(sp) else (sp[0] if sp else None)
    v1 = sp[i1] if 0 <= i1 < len(sp) else None
    dv = (v1 - v0) if (v0 is not None and v1 is not None) else None
    absorb = None
    if dv is not None and a >= 0.15 and abs(push) > 0:
        if (push > 0 and dv <= 0.02) or (push < 0 and dv >= -0.02):
            absorb = ("customers added long VIX exposure but VIX did not rise" if push > 0
                      else "customers added short VIX exposure but VIX did not fall") + " - passive side absorbing (reversal tell)"
    lm = last_m - 1 if last_m - 1 >= OPEN_M else last_m        # last FULL minute
    lv, nm = tot.get(lm, 0.0), norm.get(lm)
    if not nm:                                                  # fallback: today's median minute so far
        today = [tot.get(m, 0.0) for m in range(OPEN_M, lm)]
        nm = statistics.median(today) if len(today) >= 10 else None
        basis = "today's median minute"
    else:
        basis = f"median of {_hm(lm)} over earlier sessions"
    heavy = None
    if nm and lv >= 2 * nm and lv >= 50:
        heavy = {"t": _hm(lm), "n": round(lv), "x": round(lv / nm, 1), "basis": basis}
    return {"who": who, "strength": strength, "share": round(share, 3), "bought": round(b), "sold": round(s_),
            "lots_min": round(t / len(ms), 1), "whale_pct": round(100 * sum(wgross.get(m, 0.0) for m in ms) / (b + s_)) if b + s_ else None,
            "push": "long VIX" if push > 0 else "short VIX" if push < 0 else "flat", "vix_move": round(dv, 2) if dv is not None else None,
            "absorption": absorb, "heavy": heavy, "win_min": len(ms), "vix": v1}


def load_vix_rows(sf: Callable, trade_date: date) -> list[dict]:
    """VIX / VIXW rows of trade_date summed per minute x bucket (every session: the overnight minutes carry this
    trade_date too)."""
    from sqlalchemy import func, select

    from trading_intel.memory.models import TasCrowdMinute as T
    with sf() as s:
        q = (select(T.minute, T.bucket, func.sum(T.buy_contracts).label("b"), func.sum(T.sell_contracts).label("s"),
                    func.sum(T.other_contracts).label("o"), func.sum(T.signed_delta).label("sd"), func.max(T.spot).label("sp"))
             .where(T.trade_date == trade_date, T.root.in_(VIX_ROOTS)).group_by(T.minute, T.bucket))
        return [{"minute": r.minute, "bucket": r.bucket, "buy": float(r.b or 0), "sell": float(r.s or 0),
                 "other": float(r.o or 0), "sd": float(r.sd or 0), "spot": r.sp} for r in s.execute(q)]


def load_vix_norm(sf: Callable, trade_date: date) -> dict:
    """{minute of day: median total VIX contracts of that minute over the previous VIX_NORM_DAYS sessions}; {} when
    fewer than 3 sessions are stored."""
    from sqlalchemy import func, select

    from trading_intel.memory.models import TasCrowdMinute as T
    with sf() as s:
        days = [d for (d,) in s.execute(select(T.trade_date).where(T.trade_date < trade_date, T.root.in_(VIX_ROOTS))
                                         .group_by(T.trade_date).order_by(T.trade_date.desc()).limit(VIX_NORM_DAYS))]
        if len(days) < 3:
            return {}
        q = (select(T.trade_date, T.minute, func.sum(T.buy_contracts + T.sell_contracts + T.other_contracts))
             .where(T.trade_date.in_(days), T.root.in_(VIX_ROOTS)).group_by(T.trade_date, T.minute))
        per: dict[int, dict] = defaultdict(dict)
        for d, m, n in s.execute(q):
            per[_mins(m)][d] = float(n or 0)
    return {m: statistics.median([v.get(d, 0.0) for d in days]) for m, v in per.items() if OPEN_M <= m <= CLOSE_M}


_VIX_NORM: dict = {}


def run_vix(sf: Callable, trade_date: date, now_m: int, whale_min: int) -> dict:
    if trade_date not in _VIX_NORM:
        _VIX_NORM.clear()
        _VIX_NORM[trade_date] = load_vix_norm(sf, trade_date)
    live = vix_build(load_vix_rows(sf, trade_date), trade_date, now_m, whale_min, _VIX_NORM[trade_date])
    write_json(VIX_OUT, live)
    if live.get("ok"):
        t = live["tape"]
        print(f"whales_feed.vix date={live['date']} asof={live['asof']} whale={live['totals']['whale']:+d} crowd={live['totals']['crowd']:+d} "
              f"lots opposed={len(live['opp'])} tape={t['who']} {t['strength']}", flush=True)
    else:
        print("whales_feed.vix_skip", live.get("reason"), flush=True)
    return live


# ---------------------------------------------------------------- DB + files
def load_rows(sf: Callable, trade_date: date) -> list[dict]:
    """2026-11-21: the database adds up the day (one row per minute x bucket x 0DTE-or-not, ~1,600 rows) instead of
    shipping every minute x contract row to Python each pass (hundreds of thousands on a heavy day: a pass took
    longer than a minute and held a whole NAS core). Same numbers: minute_flows() only ever sums signed_delta per
    minute and bucket, split 0DTE vs the rest, and uses one spot per minute (now the newest print's spot)."""
    from sqlalchemy import and_, case, func, select

    from trading_intel.memory.models import TasCrowdMinute as T
    win = and_(T.trade_date == trade_date, T.root.in_(("SPX", "SPXW")),
               T.minute >= datetime.combine(trade_date, dtime(9, 30)),
               T.minute < datetime.combine(trade_date, dtime(16, 15)))
    is0 = case((T.expiry == trade_date, 1), else_=0)
    with sf() as s:
        # 2026-11-19 A1b: tas_crowd_minute also holds VIX rows and overnight / pre-market minutes -
        # this feed is SPX regular hours only.
        q = (select(T.minute, T.bucket, is0.label("z"), func.sum(T.signed_delta).label("sd"))
             .where(win).group_by(T.minute, T.bucket, is0))
        out = [{"minute": r.minute, "bucket": r.bucket, "expiry": trade_date if r.z else None,
                "signed_delta": float(r.sd or 0.0), "spot": None} for r in s.execute(q)]
        if s.get_bind().dialect.name == "postgresql":      # newest print's spot per minute
            sq = (select(T.minute, T.spot).where(win, T.spot.isnot(None))
                  .order_by(T.minute, T.last_ts.desc()).distinct(T.minute))
        else:
            sq = select(T.minute, func.max(T.spot)).where(win, T.spot.isnot(None)).group_by(T.minute)
        spots = {m: sp for m, sp in s.execute(sq)}
    for r in out:                                    # spot rows first so minute_flows sees it for every minute
        r["spot"] = spots.get(r["minute"])
    return out


def write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def run_score() -> dict:
    days = []
    for p in sorted((REP / "days").glob("*.json")):
        try:
            days.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    sc = score(days)
    write_json(REP / "score.json", sc)
    print("whales_feed.score", sc["verdict"], json.dumps(sc["scopes"]), flush=True)
    return sc


def run_once(sf: Callable, trade_date: date, now_m: int, whale_min: int, final: bool) -> dict:
    try:                                           # 2026-11-21 C3: VIX in lots; never stops the SPX feed
        run_vix(sf, trade_date, now_m, whale_min)
    except Exception as exc:  # noqa: BLE001
        print(f"whales_feed.vix ERROR: {str(exc)[:200]}", flush=True)
    live = build(load_rows(sf, trade_date), trade_date, now_m, whale_min)
    try:                                           # the running verdict, shown on the cockpit strip
        live["score"] = json.loads((REP / "score.json").read_text(encoding="utf-8")).get("verdict")
    except (OSError, ValueError):
        live["score"] = None
    write_json(OUT, live)
    if not live.get("ok"):
        print("whales_feed.skip", live.get("reason"), flush=True)
        return live
    t = live["totals"]
    print(f"whales_feed.done date={live['date']} asof={live['asof']} 0dte whale={t['0dte']['whale']:+.1f}M crowd={t['0dte']['crowd']:+.1f}M "
          f"opposed={len(live['opp']['0dte'])}", flush=True)
    if final:
        write_json(REP / "days" / (live["date"].replace("-", "") + ".json"), day_record(live))
        run_score()
    return live


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="one pass now, then exit")
    ap.add_argument("--date", help="YYYY-MM-DD (one full-day pass for that stored day, keeps its day record)")
    ap.add_argument("--score", action="store_true", help="re-score every stored day record, then exit")
    a = ap.parse_args()
    if a.score:
        run_score()
        return 0
    from trading_intel.config import get_settings
    from trading_intel.memory.db import make_session_factory

    try:                                          # 2026-11-21: the cockpit writer and page server come first on the 2-core NAS
        os.nice(10)
    except (AttributeError, OSError):
        pass
    settings = get_settings()
    sf = make_session_factory(settings)
    wm = int(getattr(settings, "TAS_WHALE_MIN_SIZE", 20))
    if a.date:
        run_once(sf, date.fromisoformat(a.date), CLOSE_M, wm, final=True)
        return 0
    if a.once:
        now = datetime.now(ET)
        run_once(sf, now.date(), _mins(now), wm, final=(now.hour, now.minute) > END_HM)
        return 0
    while True:                                   # daemon: 09:31-16:05 ET, every minute
        now = datetime.now(ET)
        hm = (now.hour, now.minute)
        if now.weekday() >= 5 or hm > END_HM:
            if hm > END_HM and now.weekday() < 5:
                try:
                    run_once(sf, now.date(), CLOSE_M, wm, final=True)
                except Exception as exc:
                    print(f"whales_feed ERROR: {str(exc)[:200]}", flush=True)
            print("whales_feed: window closed - exiting", flush=True)
            return 0
        if hm >= START_HM:
            try:
                run_once(sf, now.date(), _mins(now), wm, final=False)
            except Exception as exc:
                print(f"whales_feed ERROR: {str(exc)[:200]}", flush=True)
        time.sleep(EVERY_S - (time.time() % EVERY_S) + 3)    # always waits for the NEXT minute, never back-to-back


if __name__ == "__main__":
    sys.exit(main())

