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

Read-only on the DB, no vendor calls (rule 1), no AI (rule 7). Descriptive flow, not a signal (rule 4).
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import defaultdict
from collections.abc import Callable
from datetime import date, datetime
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


# ---------------------------------------------------------------- DB + files
def load_rows(sf: Callable, trade_date: date) -> list[dict]:
    from sqlalchemy import select

    from trading_intel.memory.models import TasCrowdMinute as T
    with sf() as s:
        q = select(T.minute, T.expiry, T.bucket, T.signed_delta, T.spot, T.buy_contracts, T.sell_contracts).where(T.trade_date == trade_date)
        return [dict(r._mapping) for r in s.execute(q)]


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
        time.sleep(EVERY_S - (time.time() % EVERY_S) + 3)


if __name__ == "__main__":
    sys.exit(main())

