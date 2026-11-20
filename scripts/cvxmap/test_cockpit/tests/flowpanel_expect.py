# ruff: noqa: E501
"""Expected numbers for flowpanel.js (2026-11-19): F1 / F2 for the last regular-hours SPX minute of a minute log, computed
independently of the page (scipy-free Black-Scholes), + the F3 label via terrain.classify.
Usage: python flowpanel_expect.py <dir with flow_YYYYMMDD.ndjson, live.json, terrain.json> <SYM>  -> <dir>/fp_expect.json"""
import glob
import json
import math
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
import terrain

d, sym = sys.argv[1], sys.argv[2]
live = json.load(open(os.path.join(d, "live.json"), encoding="utf-8"))[sym]
oi = {float(r["k"]): (r.get("cOI") or 0, r.get("pOI") or 0) for r in live["rows"]}
last = None
for line in open(sorted(glob.glob(os.path.join(d, "flow_2*.ndjson")))[-1], encoding="utf-8"):
    o = json.loads(line)
    if o.get("s") == sym and "09:30" <= o["t"] <= "16:00":
        last = o


def otm(k: float, s: float, ci: float, pi: float) -> float:
    a, b = (ci, pi) if k >= s else (pi, ci)
    return a if a > 0 else (b if b > 0 else 0)


def g_usd(s: float, k: float, sg: float, t: float) -> float:
    if not (s > 0 and k > 0 and sg > 0 and t > 0):
        return 0.0
    sq = sg * math.sqrt(t)
    d1 = (math.log(s / k) + 0.5 * sg * sg * t) / sq
    return 100 * math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi) / (s * sq) * s * s * 0.01


s = last["x"]
hh, mm = map(int, last["t"].split(":"))
t = max(960 - (hh * 60 + mm), 1) / 525600
f_oi = f_tr = o0 = 0.0
z = lambda a, i: (a[i] if a and a[i] is not None else 0) or 0  # noqa: E731
for i, k in enumerate(last["k"]):
    g = g_usd(s, k, otm(k, s, z(last["ci"], i), z(last["pi"], i)), t)
    cb, cs, pb, ps = z(last["cvb"], i), z(last["cvs"], i), z(last["pvb"], i), z(last["pvs"], i)
    if k in oi:
        f_oi += (oi[k][0] - oi[k][1]) * g
    f_tr += (-(cb - cs) - (pb - ps)) * g
    o0 += (cb - cs) - (pb - ps)
tj = json.load(open(os.path.join(d, "terrain.json")))
ts = tj["syms"].get(sym)
lab = terrain.classify({float(k): v for k, v in ts["m"].items()}, s, ts["bin"], ts["hvn"], ts["lvn"]) if ts else None
json.dump({"t": last["t"], "x": s, "oi": f_oi, "tr": f_tr, "o0": o0, "ter": lab}, open(os.path.join(d, "fp_expect.json"), "w"))
print(last["t"], s, f_oi, f_tr, o0, lab)
