# ruff: noqa: E501
"""Independent Python for the 0DTE IV the main map uses (2026-11-21): quote filter (no bid, ask <= bid, or spread > 60% of
mid -> that side gives no IV), out-of-the-money side, re-expressed from the feed's IV clock (live.json SPX.iv_end, old
feeds 16:15) to the map clock (16:00): sigma' = sigma * sqrt((iv_end - t) / (16:00 - t)), then a strike with no IV gets one
linearly from its nearest strikes that have one. Usage: python iv0_expect.py <dir> -> <dir>/iv0_expect.json"""
import json
import math
import os
import sys

d = sys.argv[1]
L = json.load(open(os.path.join(d, "live.json")))["SPX"]
S = L["spot"]
end = L.get("iv_end") or 16 * 60 + 15
tobs = end - L["mins_to_close"]
MAP_T1 = 16 * 60


def ok(b, a):
    return b is not None and a is not None and b > 0 and a > b and (a - b) / ((a + b) / 2) <= 0.6


out = []
rej = 0
for r in L["rows"]:
    k = r["k"]
    c, p = r.get("cIV") or 0, r.get("pIV") or 0
    has_q = any(r.get(x) is not None for x in ("cB", "cA", "pB", "pA"))
    if has_q:
        if c > 0 and not ok(r.get("cB"), r.get("cA")):
            c, rej = 0, rej + 1
        if p > 0 and not ok(r.get("pB"), r.get("pA")):
            p, rej = 0, rej + 1
    a, b = (c, p) if k >= S else (p, c)
    sg = a if a > 0 else (b if b > 0 else 0)
    if sg > 0 and end != MAP_T1 and tobs < MAP_T1:
        sg = sg * math.sqrt(max(end - tobs, 1) / max(MAP_T1 - tobs, 1))
    out.append([k, sg])
okk = [x[1] > 0 for x in out]
fill = 0
for i, (k, sg) in enumerate(out):
    if okk[i]:
        continue
    a, b = i - 1, i + 1
    while a >= 0 and not okk[a]:
        a -= 1
    while b < len(out) and not okk[b]:
        b += 1
    v = 0
    if a >= 0 and b < len(out):
        v = out[a][1] + (out[b][1] - out[a][1]) * (k - out[a][0]) / (out[b][0] - out[a][0])
    elif a >= 0:
        v = out[a][1]
    elif b < len(out):
        v = out[b][1]
    if v > 0:
        out[i][1] = v
        fill += 1
json.dump({"spot": S, "iv_end": end, "tobs": tobs, "rej": rej, "fill": fill, "sg": {(str(int(k)) if float(k).is_integer() else str(k)): v for k, v in out}}, open(os.path.join(d, "iv0_expect.json"), "w"))
print("iv_end", end, "rejected sides", rej, "filled", fill, "strikes", len(out))
