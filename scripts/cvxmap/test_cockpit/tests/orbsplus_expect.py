"""Independent numbers for tests/orbsplus.js (2026-11-21): from wide_YYYYMMDD.json (decoded with wide_feed.decode_slots),
book All (0DTE + 1-7 DTE), OI + signed: charm ES/h of the last minute at/before 15:50, gamma $bn at spot of the last minute,
and the strike with the biggest one-minute traded volume (calls + puts) of the day.
Usage: python orbsplus_expect.py <wide_YYYYMMDD.json> <SYM> <out.json>"""
import json, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import wide_feed as W
J = json.load(open(sys.argv[1])); s = J["syms"][sys.argv[2]]
sl = W.decode_slots(s["slots"], s.get("step", 1))
def arr(b, f): return [(b.get(f + "0") or [0] * len(b["k"]))[i] + (b.get(f + "7") or [0] * len(b["k"]))[i] for i in range(len(b["k"]))]
def tm(t): h, m = map(int, t.split(":")); return h * 60 + m
es = g = None
for b in sl:
    x = b["x"]; ch = sum(a + c for a, c in zip(arr(b, "ch"), arr(b, "cht"))); gm = sum(a + c for a, c in zip(arr(b, "g"), arr(b, "gt")))
    if tm(b["t"]) <= 950: es = -ch * 1e9 / (50 * x)
    g = gm
best = None; prev = None; pt = None
for b in sl:
    cur = {k: (c, p) for k, c, p in zip(b["k"], arr(b, "c"), arr(b, "p"))}
    if prev is None or tm(b["t"]) - tm(pt) > 3:      # first minute / collector gap: unknown minutes, skipped (as the page)
        prev, pt = cur, b["t"]; continue
    for k, (c, p) in cur.items():
        pc, pp = prev.get(k, (0, 0)) if prev else (0, 0)
        n = max(0, c - pc) + max(0, p - pp)
        if best is None or n > best[0]: best = (n, k)
    prev, pt = cur, b["t"]
k = best[1]; ks = str(int(k)) if float(k).is_integer() else str(k)
json.dump({"es_last": es, "g_last": g, "big_k": ks, "big_n": best[0]}, open(sys.argv[3], "w")); print(open(sys.argv[3]).read())
