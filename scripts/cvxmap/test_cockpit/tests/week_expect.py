"""Independent Python for the WK Week IV checks (2026-11-20). Reads a served folder's live.json + ti.json and writes
wk_expect.json: per-expiry forward basis from the live Week deltas, the 08:35 delta-weighted IV per contract, the final
Week-book IV at the live minute (live IV after 09:35, rescaled from whole days to real time), and the Week gamma at spot
at the forward. Usage: python week_expect.py <folder> [out.json]"""
import json, math, sys, os, datetime as dt
from statistics import NormalDist
N = NormalDist()
D = sys.argv[1]; OUT = sys.argv[2] if len(sys.argv) > 2 else "wk_expect.json"
L = json.load(open(os.path.join(D, "live.json"))); T = json.load(open(os.path.join(D, "ti.json")))
S = L["SPX"]["spot"]; ts = L["ts"]; day = ts[:10]; now = int(ts[11:13]) * 60 + int(ts[14:16])
ET = dt.timezone(dt.timedelta(hours=-5))   # November: EST
def inst(iso, mins): y, m, d = map(int, iso.split("-")); return dt.datetime(y, m, d, tzinfo=ET) + dt.timedelta(minutes=mins)
def exp_inst(r):
    et = r.get("exp_t") or (r["e"] + "T16:00")
    return dt.datetime.fromisoformat(et) if ("+" in et[10:] or "-" in et[16:]) else inst(et[:10], int(et[11:13]) * 60 + int(et[14:16]))
def tau_min(r, t):
    et = r.get("exp_t") or (r["e"] + "T16:00")
    if et[:10] < day or (et[:10] == day and r.get("set", "PM") != "AM"): return None
    m = (exp_inst(r) - inst(day, t)).total_seconds() / 60; return m if m > 0 else None
# basis
by = {}
for r in L["week"]["SPX"]["rows"]:
    if abs(r["k"] - S) > 40 or not (r.get("dte") or 0) >= 1: continue
    tau = r["dte"] * 1440 / 525600; sq = math.sqrt(tau)
    for iv, Dl, c in ((r.get("cIV"), r.get("cD"), 1), (r.get("pIV"), r.get("pD"), 0)):
        if not (iv and iv > 0) or Dl is None: continue
        dd = Dl if c else Dl + 1
        if not 0.1 < dd < 0.9: continue
        d1 = N.inv_cdf(dd); F = r["k"] * math.exp(d1 * iv * sq - 0.5 * iv * iv * tau)
        by.setdefault(r["e"] + "|" + r.get("set", "PM"), {"dte": r["dte"], "v": []})["v"].append(F - S)
B = {}
for k, o in by.items():
    if len(o["v"]) >= 6: v = sorted(o["v"]); B[k] = {"dte": o["dte"], "b": v[len(v) // 2]}
def b_of(r):
    k = r["e"] + "|" + r.get("set", "PM")
    if k in B: return B[k]["b"]
    best = None
    for v in B.values():
        if best is None or abs(v["dte"] - (r.get("dte") or 1)) < abs(best["dte"] - (r.get("dte") or 1)): best = v
    return best["b"] if best else 0.0
valid = lambda x: 0.03 <= x <= 1.5
blend, final, fb = {}, {}, {}
LW = {r["e"] + "|" + r.get("set", "PM") + "|" + str(r["k"]): r for r in L["week"]["SPX"]["rows"]}
def otm(k, c, p): a, b = (c, p) if k >= S else (p, c); return a if a > 0 else (b if b > 0 else 0)
key = lambda r: r["e"] + "|" + r.get("set", "PM") + "|" + str(int(r["k"]) if float(r["k"]).is_integer() else r["k"])
for r in T["near_book"]["SPX"]["rows"]:
    c, p = r.get("cIV") or 0, r.get("pIV") or 0
    if not (valid(c) and valid(p) and abs(c - p) <= 0.10): continue      # the cases WK itself decides (BQ handles the rest)
    t = tau_min(r, max(now, 480))
    if not t: continue
    F = S + b_of(r); sm = (c + p) / 2; w = N.cdf(math.log(F / r["k"]) / (sm * math.sqrt(t / 525600)))
    blend[key(r)] = w * p + (1 - w) * c; fb[key(r)] = F - S
    lr = LW.get(r["e"] + "|" + r.get("set", "PM") + "|" + str(r["k"]))
    if lr and now >= 9 * 60 + 35 and (lr.get("dte") or 0) >= 1:
        sl = otm(r["k"], lr.get("cIV") or 0, lr.get("pIV") or 0); tt = tau_min(r, now)
        if sl > 0 and tt: final[key(r)] = max(0.01, sl * math.sqrt(lr["dte"] * 1440 / tt))
json.dump({"spot": S, "now": now, "basis": {k: v["b"] for k, v in B.items()}, "blend": blend, "final_live": final, "fb": fb},
          open(OUT, "w"))
print("basis", {k: round(v["b"], 2) for k, v in sorted(B.items())}, "blend", len(blend), "live", len(final))
