# ruff: noqa: E501
"""Expected provenance counts for bookclean.js (2026-11-20): the BQ v0 rules re-implemented independently in Python
on ti.json near_book + live.json spot. Usage: python book_expect.py <dir> <SYM> -> <dir>/bq_expect.json"""
import json
import os
import sys

LO, HI, GAP, REACH = 0.03, 1.5, 0.10, 3
d, sym = sys.argv[1], sys.argv[2]
rows = json.load(open(os.path.join(d, "ti.json")))["near_book"][sym]["rows"]
S = json.load(open(os.path.join(d, "live.json")))[sym]["spot"]
ok = lambda x: LO <= x <= HI  # noqa: E731
by: dict = {}
for r in rows:
    by.setdefault((r["e"], r.get("set") or "PM"), []).append(r)
cnt = {"observed": 0, "conflict": 0, "paired": 0, "interpolated": 0, "unavailable": 0, "rows": 0}
sgs = {}
for rs in by.values():
    rs.sort(key=lambda r: r["k"])
    res = []
    for r in rs:
        c, p = r["cIV"] or 0, r["pIV"] or 0
        otm_is_call = r["k"] >= S
        if ok(c) and ok(p):
            res.append(["observed", c if otm_is_call else p] if abs(c - p) <= GAP else ["conflict", None, c, p])
        elif ok(c) or ok(p):
            v = c if ok(c) else p
            res.append(["observed" if (ok(c) if otm_is_call else ok(p)) else "paired", v])
        else:
            res.append(["none", None])
    obs = [i for i, x in enumerate(res) if x[0] == "observed"]
    for i, x in enumerate(res):
        if x[0] != "conflict":
            continue
        a = max([j for j in obs if j < i], default=None)
        b = min([j for j in obs if j > i], default=None)
        if a is not None and b is not None:
            ref = res[a][1] + (res[b][1] - res[a][1]) * (rs[i]["k"] - rs[a]["k"]) / ((rs[b]["k"] - rs[a]["k"]) or 1)
        else:
            ref = res[a][1] if a is not None else (res[b][1] if b is not None else None)
        c, p = x[2], x[3]
        x[1] = (c if rs[i]["k"] >= S else p) if ref is None else (c if abs(c - ref) <= abs(p - ref) else p)
    for i, x in enumerate(res):
        if x[0] != "none":
            continue
        a, b = i - 1, i + 1
        while a >= 0 and not (res[a][1] and res[a][0] != "interpolated"):
            a -= 1
        while b < len(res) and not (res[b][1] and res[b][0] != "interpolated"):
            b += 1
        if a >= 0 and b < len(res) and i - a <= REACH and b - i <= REACH:
            x[1] = res[a][1] + (res[b][1] - res[a][1]) * (rs[i]["k"] - rs[a]["k"]) / ((rs[b]["k"] - rs[a]["k"]) or 1)
            x[0] = "interpolated"
        else:
            x[0] = "unavailable"
    for r, x in zip(rs, res, strict=True):
        cnt["rows"] += 1
        cnt[x[0]] += 1
        sgs["%s|%s|%s" % (r["e"], r.get("set") or "PM", int(r["k"]) if float(r["k"]).is_integer() else r["k"])] = x[1] if x[0] != "unavailable" else 0
json.dump({"spot": S, "counts": cnt, "sg": sgs}, open(os.path.join(d, "bq_expect.json"), "w"))
print(S, cnt)
