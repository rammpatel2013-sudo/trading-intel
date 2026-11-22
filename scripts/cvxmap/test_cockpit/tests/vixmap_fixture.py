# ruff: noqa: N812
"""Fixture for vixmap.js (2026-11-19): a synthetic VIX session 09:30-12:45 ->
<out>/vixmap_20261106.json (file v3 with the strike book) + wide_index.json.
Uses the fake VIX pull of tests/cvxmap/test_vix_map.py. Usage: python vixmap_fixture.py <out_dir>"""
import datetime
import json
import math
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts", "cvxmap"))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import cvx_live as L  # noqa: E402
import vix_map as V  # noqa: E402
import whales_feed as WF  # noqa: E402

from tests.cvxmap.test_vix_map import make_vix_rec  # noqa: E402

out = sys.argv[1]
# C4 (2026-11-21): three earlier sessions with volume by VIX level, so 11/06 carries zones + naked POCs
for k, (ymd, base) in enumerate((("20261103", 19.6), ("20261104", 18.4), ("20261105", 16.1))):
    pd = V.VixDay(ymd)
    d0 = datetime.datetime(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:]), 9, 30)
    for i in range(120):
        now = d0 + datetime.timedelta(minutes=i)
        pd.feed(L.decode(make_vix_rec(now.isoformat(), vix=round(base + 0.3 * math.sin(i / 15 + k), 2)))["VIX"], now)
    P = pd.payload()
    # the fake pull's volume is flat after its first minute, so set a readable profile by hand: demand near 17.0,
    # supply near 18.2, and one POC per day (11/03 at 18.95 stays naked: no later range reaches it)
    vp: dict = {}
    for j in range(-12, 13):
        V.vp_add(vp, 17.0 + j * 0.05, 900 * math.exp(-abs(j) / 3) * (1.6 if k == 1 else 1), 600 * math.exp(-abs(j) / 3), 200)
        V.vp_add(vp, 18.2 + j * 0.05, 700 * math.exp(-abs(j) / 2.5), 150, 500 * math.exp(-abs(j) / 2.5))
    V.vp_add(vp, (18.95, 17.0, 16.3)[k], 2500, (800, 1800, 1700)[k], (1700, 700, 800)[k])
    P["vp"], P["rng"] = V.vp_payload(vp), [(18.9, 16.8, 16.0)[k], (19.5, 17.4, 16.6)[k]]
    with open(os.path.join(out, f"vixmap_{ymd}.json"), "w") as fh:
        json.dump(P, fh, separators=(",", ":"))
vd = V.VixDay("20261106")
vd.zones = V.zones_from_dir(out, "20261106")
t = datetime.datetime(2026, 11, 6, 9, 30)
for i in range(196):
    now = t + datetime.timedelta(minutes=i)
    vix = 17.2 + 1.4 * math.sin(i / 40) + 0.15 * math.sin(i / 3)
    rec = make_vix_rec(now.isoformat(), vix=round(vix, 2))
    vd.feed(L.decode(rec)["VIX"], now)
with open(os.path.join(out, "vixmap_20261106.json"), "w") as fh:
    json.dump(vd.payload(), fh, separators=(",", ":"))
with open(os.path.join(out, "wide_index.json"), "w") as fh:
    json.dump({"vix_days": ["20261106"]}, fh)
# C3 (2026-11-21): vix_whales_live.json for the same day (whales buy early, the crowd sells; sellers win late)
rows = []
for i in range(196):
    mm = datetime.datetime(2026, 11, 6, 9, 30) + datetime.timedelta(minutes=i)
    vix = 17.2 + 1.4 * math.sin(i / 40) + 0.15 * math.sin(i / 3)
    w = 60 if i < 90 else 0
    rows.append({"minute": mm, "bucket": "whale", "buy": w, "sell": 0 if i < 90 else 40, "other": 0, "sd": 0, "spot": vix})
    rows.append({"minute": mm, "bucket": "crowd", "buy": 20 + i % 7, "sell": 45 + (i * 3) % 11, "other": 2,
                 "sd": -300, "spot": vix})
rows.append({"minute": datetime.datetime(2026, 11, 6, 8, 15), "bucket": "crowd", "buy": 30, "sell": 12, "other": 0,
             "sd": 0, "spot": None})
J = WF.vix_build(rows, datetime.date(2026, 11, 6), 9 * 60 + 30 + 195, 20, {9 * 60 + 30 + 193: 20.0})
with open(os.path.join(out, "vix_whales_live.json"), "w") as fh:
    json.dump(J, fh, separators=(",", ":"))
print("zones", [(z["kind"], z["lo"], z["hi"]) for z in vd.zones["zones"]], "naked", vd.zones["naked"][:3])
print("whales", J["totals"], len(J["opp"]), J["tape"]["who"], J["tape"]["strength"])
print("books", [b["t"] for b in vd.payload()["books"]])
