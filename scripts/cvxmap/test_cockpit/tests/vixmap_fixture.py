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
import cvx_live as L  # noqa: E402
import vix_map as V  # noqa: E402

from tests.cvxmap.test_vix_map import make_vix_rec  # noqa: E402

out = sys.argv[1]
vd = V.VixDay("20261106")
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
print("books", [b["t"] for b in vd.payload()["books"]])
