#!/usr/bin/env python3
"""smile_beta.py - created 2026-11-06.

Measured spot-vol betas for the cockpit's Smile button: how much the IV of a FIXED strike
really moved for each +1% SPX move, by distance from spot and by days to expiry.

  0DTE      intraday, 15-minute pairs from the minute flow files (same pairs as alt_score T2),
            last DAYS0 stored sessions, buckets of log-moneyness: <-0.5%, -0.5..0, 0..0.5, >0.5%
            (strikes within 1% of spot; the cockpit uses the wing value beyond that).
  1+ DTE    day over day, SPX surface_snapshots (17:08 EOD, every strike, expiries up to ~19 DTE),
            last DAYSM sessions, tenors 1-3 / 4-9 / 10+ days, buckets <-2, -2..-1, -1..0, 0..1,
            1..2, >2 % from spot.

Writes scripts/cvxmap/data/smilebeta.json. The cockpit's Smile button uses these numbers
(sigma_S = beta / S per strike) and falls back to the old sticky-moneyness formula when the file is
missing or a bucket has too few pairs. Replaced the formula 2026-11-06 at Mithil's request;
to go back, set SMILE_MEASURED=false in cvx_cockpit.html.

Usage (NAS):  bash scripts/nas/run_script.sh smile_beta      (nightly, after the surface snapshot)
Descriptive only (rule 4).
"""
from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import alt_score as A  # noqa: E402  (same folder: load_flow_spx, t2_pairs, bucket)

DATA = HERE / "cvxmap" / "data"
DAYS0 = 20                      # intraday sessions for 0DTE
DAYSM = 120                     # calendar days of surface history for 1+ DTE
MIN_N0 = 150                    # pairs needed per 0DTE bucket
MIN_NM = 300                    # pairs needed per multi-day cell
CLIP = (-15.0, 6.0)             # vol pts per +1%, sanity clip
B0 = ["put wing", "near put", "near call", "call wing"]
EDGES_M = [-2.0, -1.0, 0.0, 1.0, 2.0]          # % from spot -> 6 buckets
TENORS = [(1, 3), (4, 9), (10, 9999)]


def slope(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    sx, sy = sum(xs), sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))
    den = n * sxx - sx * sx
    return (n * sxy - sx * sy) / den if den > 0 else None


def clip(v):
    return None if v is None else round(max(CLIP[0], min(CLIP[1], v)), 2)


def env_val(name):
    v = os.environ.get(name)
    if v:
        return v
    try:
        m = re.search(rf'^{name}\s*=\s*"?([^"\n]+?)"?\s*$', (HERE.parent / ".env").read_text(), re.M)
        return m.group(1).strip() if m else ""
    except OSError:
        return ""


def db_connect():
    url = env_val("DATABASE_URL").replace("postgresql+psycopg://", "postgresql://").replace(
        "postgresql+psycopg2://", "postgresql://")
    try:
        import psycopg  # type: ignore
        return psycopg.connect(url)
    except ImportError:
        import psycopg2  # type: ignore
        return psycopg2.connect(url)


def zero_dte(data: Path):
    files = sorted(glob.glob(str(data / "flow_2*.ndjson")))[-DAYS0:]
    by = {b: ([], []) for b in B0}
    days = []
    for f in files:
        ymd = os.path.basename(f)[5:13]
        iso = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"
        flow = A.load_flow_spx(f, iso)
        if len(flow) < 120:
            continue
        recs = A.t2_pairs(flow)
        if not recs:
            continue
        days.append(iso)
        for r in recs:
            xs, ys = by[r["b"]]
            xs.append(r["dS"] * 100.0)          # % SPX move
            ys.append(r["dv"] * 100.0)          # vol pts
    beta, n = [], []
    for b in B0:
        xs, ys = by[b]
        s = slope(xs, ys) if len(xs) >= MIN_N0 else None
        beta.append(clip(s))
        n.append(len(xs))
    return {"edges_pct": [-0.5, 0.0, 0.5], "beta": beta, "n": n, "days": days,
            "how": "15-minute pairs, strikes within 1% of spot, pooled over the sessions listed"}


def multi_day():
    conn = db_connect()
    cur = conn.cursor()
    since = datetime.date.today() - datetime.timedelta(days=DAYSM)
    cur.execute("""select ts::date, expiry_date, strike, iv, spot, dte from surface_snapshots
                   where symbol='SPX' and ts::date >= %s and iv > 0.03 and iv < 1.5""", (since,))
    rows = cur.fetchall()
    conn.close()
    book, spot = {}, {}
    for d, e, k, iv, s, dte in rows:
        book[(d, e, float(k))] = (float(iv), int(dte))
        spot[d] = float(s)
    days = sorted(spot)
    nxt = dict(zip(days[:-1], days[1:]))
    cells = {(ti, bi): ([], []) for ti in range(len(TENORS)) for bi in range(len(EDGES_M) + 1)}
    for (d, e, k), (iv, dte) in book.items():
        d2 = nxt.get(d)
        if d2 is None or dte < 1:
            continue
        b2 = book.get((d2, e, k))
        if not b2:
            continue
        ti = next((i for i, (lo, hi) in enumerate(TENORS) if lo <= dte <= hi), None)
        if ti is None:
            continue
        m = 100.0 * (k / spot[d] - 1.0)
        bi = sum(1 for x in EDGES_M if m >= x)
        xs, ys = cells[(ti, bi)]
        xs.append(100.0 * (spot[d2] / spot[d] - 1.0))
        ys.append(100.0 * (b2[0] - iv))
    beta = [[clip(slope(*cells[(ti, bi)]) if len(cells[(ti, bi)][0]) >= MIN_NM else None)
             for bi in range(len(EDGES_M) + 1)] for ti in range(len(TENORS))]
    n = [[len(cells[(ti, bi)][0]) for bi in range(len(EDGES_M) + 1)] for ti in range(len(TENORS))]
    return {"tenors": TENORS, "edges_pct": EDGES_M, "beta": beta, "n": n,
            "days": [days[0].isoformat(), days[-1].isoformat()] if days else None, "sessions": len(days),
            "how": "close-to-close pairs of the same strike + expiry, both directions pooled"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(DATA), help="folder with flow_*.ndjson")
    ap.add_argument("--out", default=str(DATA / "smilebeta.json"))
    ap.add_argument("--no-db", action="store_true")
    a = ap.parse_args()
    out = {"generated": datetime.datetime.now().isoformat(timespec="minutes"),
           "units": "IV vol points per +1% SPX move, fixed strike",
           "dte0": zero_dte(Path(a.data))}
    if not a.no_db:
        try:
            out["multi"] = multi_day()
        except Exception as e:  # noqa: BLE001
            print("multi-day betas skipped:", e)
    p = Path(a.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, separators=(",", ":")))
    tmp.replace(p)
    z = out["dte0"]
    print("smilebeta.json  0DTE", dict(zip(B0, z["beta"])), "n", z["n"], "days", len(z["days"]))
    if "multi" in out:
        for (lo, hi), row in zip(TENORS, out["multi"]["beta"]):
            print(f"  {lo}-{hi if hi < 9999 else '+'} DTE", row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
