"""
p1score.py - created 2026-11-04. Phase 1 scoring for the nightly replay (scripts/cvx_replay.py).

All rules are fixed here BEFORE results (pre-registered); change only with a dated note.
  #15 predictions   Density run at each full hour 10:00-15:00: close zone, median error vs the
                    naive "close = spot now", P10/P90 vs the rest-of-day low/high; vanna top/bottom
                    at that hour vs the rest-of-day high/low.
      boundary rule Mithil 2026-10-26: long gamma -> vanna UPPER caps the top, gamma LOWER floors the
                    bottom; short gamma -> vanna LOWER floors, gamma UPPER caps. Levels from the
                    page's own 0DTE map (view basis); LOCK = first 10 consecutive 2-min columns all
                    within +-2 pts of that window's median; scored after the lock: error vs the
                    rest-of-day extreme and containment %.
  #17 levels        touch = within 2 pts; held = after the touch, price goes 10 pts back to the side
                    it came from before breaking; broke = 3 consecutive 1-min closes 5+ pts through.
  #85 flip          ours = the page's gamma zero nearest spot each 2-min column; Doc's = the
                    published pivot. vol-split = mean |1-min move| below / above the line;
                    placebo = same ratio for the line +-25 and +-50 pts; if-then = mean 30-min
                    range after a cross down vs after a cross up vs any 30-min window.
  #16 gate / IV     net/gross gamma at spot every 10 min (0DTE and all expiries, the page's own
                    greekCol); flip crossings split by the day's median gate: 15-min follow-through.
                    Vendor IV vs our parity IV: gamma zero (same positions) under each, every 5 min.
  seq / W-M         the page's own seqEvents (its own ✓/✗ at its own target) and W/M arrows
                    scored here at 30 min: right if price moved in the arrow's direction.
"""
import math
import statistics as stx

HOURS = (600, 660, 720, 780, 840, 900)


def hh(m):
    return "%02d:%02d" % (m // 60, m % 60)


def mm(t):
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def path_of(spx_flow):
    """{HH:MM: {x}} -> [(minute, x)] 09:31-16:00."""
    p = []
    for t, j in spx_flow.items():
        if "09:31" <= t <= "16:00" and j.get("x"):
            p.append((mm(t), float(j["x"])))
    p.sort()
    return p


def _after(path, t):
    return [x for (m, x) in path if m >= t]


def _med(a):
    a = [v for v in a if v is not None]
    return stx.median(a) if a else None


def _mean(a):
    a = [v for v in a if v is not None]
    return sum(a) / len(a) if a else None


# ------------------------------------------------------------------ #15 predictions
def predictions(den_runs, cols, path, close):
    by_t = {}
    for r in den_runs or []:
        try:
            by_t[mm(r["t"])] = r
        except Exception:
            continue
    rows = []
    for h in HOURS:
        cand = [t for t in by_t if h <= t <= h + 10]
        rest = _after(path, h)
        if not rest:
            continue
        hi, lo = max(rest), min(rest)
        spot = rest[0]
        row = {"t": hh(h), "spot": spot, "naive_err": abs(spot - close), "rest_hi": hi, "rest_lo": lo}
        if cand:
            r = by_t[min(cand)]
            z = r.get("zone")
            row.update(med=r.get("med"), p10=r.get("p10"), p90=r.get("p90"), zone=z,
                       med_err=abs(r["med"] - close) if r.get("med") is not None else None,
                       zone_hit=(z is not None and z <= close < z + 5),
                       inside=(r.get("p10") is not None and r["p10"] <= close <= r["p90"]),
                       top_err=(r["p90"] - hi) if r.get("p90") is not None else None,
                       bot_err=(r["p10"] - lo) if r.get("p10") is not None else None)
        c = _col_at(cols, h)
        if c and c.get("S"):
            up = [z for z in c["vz"] if z > c["S"]]
            dn = [z for z in c["vz"] if z < c["S"]]
            if up:
                row["vt"], row["vt_err"] = min(up), min(up) - hi
            if dn:
                row["vb"], row["vb_err"] = max(dn), max(dn) - lo
        rows.append(row)
    return rows


def _col_at(cols, m):
    best = None
    for c in cols or []:
        if c["t"] <= m:
            best = c
    return best


# ------------------------------------------------------------------ boundary rule
def _lock(series):
    """series [(t, level or None)] on 2-min columns -> (lock_t, level) or (None, None)."""
    for i in range(len(series) - 9):
        w = series[i:i + 10]
        if any(v is None for _, v in w):
            continue
        if any(w[q + 1][0] - w[q][0] != 2 for q in range(9)):
            continue
        md = stx.median(v for _, v in w)
        if all(abs(v - md) <= 2 for _, v in w):
            return w[-1][0], md
    return None, None


def boundary(cols, path):
    cs = [c for c in cols or [] if 572 <= c["t"] <= 958 and c.get("S")]
    if not cs or not path:
        return None
    gsv = [c["gs"] for c in cs if c.get("gs") is not None]
    regime = None
    if gsv:
        pos = sum(1 for v in gsv if v > 0) / len(gsv)
        regime = "long" if pos >= 0.5 else "short"
    pick = {
        "vanna_upper": lambda c: min([z for z in c["vz"] if z > c["S"]], default=None),
        "vanna_lower": lambda c: max([z for z in c["vz"] if z < c["S"]], default=None),
        "gamma_upper": lambda c: min([z for z in c["gz"] if z > c["S"]], default=None),
        "gamma_lower": lambda c: max([z for z in c["gz"] if z < c["S"]], default=None),
    }
    binding = {"long": ("vanna_upper", "gamma_lower"), "short": ("gamma_upper", "vanna_lower")}.get(regime, ())
    out = {"regime": regime, "pos_share": round(sum(1 for v in gsv if v > 0) / len(gsv), 3) if gsv else None, "lines": {}}
    for name, f in pick.items():
        t_l, lv = _lock([(c["t"], f(c)) for c in cs])
        r = {"rule_says_binds": name in binding, "lock": hh(t_l) if t_l else None, "level": lv}
        if t_l:
            rest = _after(path, t_l)
            if rest:
                if name.endswith("upper"):
                    r["err"] = round(lv - max(rest), 2)
                    r["contain"] = round(100.0 * sum(1 for x in rest if x <= lv) / len(rest), 1)
                else:
                    r["err"] = round(lv - min(rest), 2)
                    r["contain"] = round(100.0 * sum(1 for x in rest if x >= lv) / len(rest), 1)
        out["lines"][name] = r
    return out


# ------------------------------------------------------------------ #17 levels
def score_level(path, L):
    """-> {touch, result: held/broke/touched/untouched, t_result}."""
    if not path:
        return {"result": "no data"}
    side0 = 1 if path[0][1] > L else -1
    touch = None
    for i, (m, x) in enumerate(path):
        if abs(x - L) <= 2:
            touch = i
            break
        side0 = 1 if x > L else -1
    if touch is None:
        crossed = any((x - L) * side0 < 0 for _, x in path)
        return {"result": "untouched" if not crossed else "gapped through", "dist": round(min(abs(x - L) for _, x in path), 1)}
    run = 0
    for m, x in path[touch:]:
        if (x - L) * side0 >= 10:
            return {"touch": hh(path[touch][0]), "result": "held", "t": hh(m)}
        run = run + 1 if (L - x) * side0 >= 5 else 0
        if run >= 3:
            return {"touch": hh(path[touch][0]), "result": "broke", "t": hh(m)}
    return {"touch": hh(path[touch][0]), "result": "touched"}


def outside_levels(path, doc, gs, cone0, own=None):
    lv = []
    for src, plan in (("Doc", doc), ("Ours", own)):     # 2026-11-05: our AM levels scored next to Doc's
        if not plan:
            continue
        for l in (plan.get("levels") or {}).get("SPX") or []:
            if l.get("v"):
                lv.append((src, "%s (%s)" % (l.get("label", ""), l.get("role", "")), float(l["v"])))
        for r in plan.get("rails") or []:
            for k in ("lo", "hi"):
                if r.get(k):
                    lv.append((src + " rail", "%s %s" % (r.get("name", ""), k), float(r[k])))
    if gs.get("open") and gs.get("am_straddle"):
        lv += [("Straddle", "open + AM straddle", gs["open"] + gs["am_straddle"]), ("Straddle", "open − AM straddle", gs["open"] - gs["am_straddle"])]
    if gs.get("prev_close") and gs.get("on_straddle"):
        lv += [("Straddle", "prior close + overnight straddle", gs["prev_close"] + gs["on_straddle"]),
               ("Straddle", "prior close − overnight straddle", gs["prev_close"] - gs["on_straddle"])]
    if cone0 and cone0.get("p10") is not None:
        lv += [("Density", "09:31 cone P90", cone0["p90"]), ("Density", "09:31 cone P10", cone0["p10"])]
    out = []
    for src, name, L in lv:
        r = score_level(path, L)
        r.update(src=src, name=name, level=round(L, 2))
        out.append(r)
    return out


def doc_flip(doc):
    if not doc:
        return None
    if doc.get("pivot"):
        return float(doc["pivot"])
    for l in (doc.get("levels") or {}).get("SPX") or []:
        if l.get("role") == "piv" or "flip" in str(l.get("label", "")).lower():
            return float(l["v"])
    return None


# ------------------------------------------------------------------ #85 flip
def flip_tests(path, line_at):
    """line_at(minute) -> level or None."""
    if len(path) < 40:
        return None

    def split(off):
        up, dn = [], []
        for i in range(1, len(path)):
            m0, x0 = path[i - 1]
            L = line_at(m0)
            if L is None or path[i][0] - m0 > 2:
                continue
            (up if x0 > L + off else dn).append(abs(path[i][1] - x0))
        a, b = _mean(up), _mean(dn)
        return {"n_above": len(up), "n_below": len(dn), "above": a, "below": b, "ratio": (b / a) if a and b else None}

    real = split(0)
    plac = [split(o)["ratio"] for o in (-50, -25, 25, 50)]
    plac = [v for v in plac if v is not None]
    xs = [x for _, x in path]

    def rng30(i):
        w = [x for (m, x) in path[i:] if m - path[i][0] <= 30]
        return max(w) - min(w) if len(w) > 5 else None
    up, dn = [], []
    for i in range(1, len(path)):
        L0, L1 = line_at(path[i - 1][0]), line_at(path[i][0])
        if L0 is None or L1 is None:
            continue
        a, b = path[i - 1][1] - L0, path[i][1] - L1
        if a > 0 >= b:
            dn.append((path[i][0], rng30(i)))
        elif a < 0 <= b:
            up.append((path[i][0], rng30(i)))
    base = _mean([rng30(i) for i in range(0, len(path) - 30, 5)])
    return {"split": real, "placebo_ratio_mean": _mean(plac), "placebo_n": len(plac),
            "cross_down_n": len(dn), "cross_down_rng30": _mean([v for _, v in dn]),
            "cross_up_n": len(up), "cross_up_rng30": _mean([v for _, v in up]), "any_rng30": base,
            "crosses": [(hh(m), "down") for m, _ in dn] + [(hh(m), "up") for m, _ in up], "_xs": len(xs)}


# ------------------------------------------------------------------ #16 gate + IV variants
def gate_crossings(path, line_at, gate):
    if not gate or not path:
        return None
    g = {r["t"]: r for r in gate}
    med = _med([r.get("r1") for r in gate])

    def gate_at(m):
        ks = [t for t in g if t <= m]
        return g[max(ks)].get("r1") if ks else None
    hi, lo = [], []
    for i in range(1, len(path)):
        L0, L1 = line_at(path[i - 1][0]), line_at(path[i][0])
        if L0 is None or L1 is None:
            continue
        a, b = path[i - 1][1] - L0, path[i][1] - L1
        if (a > 0) == (b > 0) or a == 0:
            continue
        d = 1 if b > 0 else -1
        fut = [x for (m, x) in path[i:] if m - path[i][0] <= 15]
        if len(fut) < 5:
            continue
        ft = d * (fut[-1] - path[i][1])
        gv = gate_at(path[i][0])
        if gv is None or med is None:
            continue
        (hi if gv >= med else lo).append(ft)
    return {"gate_median": med, "gate_open": _med([r.get("r1") for r in gate if r["t"] <= 600]),
            "gate_close": _med([r.get("r1") for r in gate if r["t"] >= 900]),
            "r0_median": _med([r.get("r0") for r in gate]),
            "hi_n": len(hi), "hi_ft": _mean(hi), "lo_n": len(lo), "lo_ft": _mean(lo)}


def iv_variants(viv, spx_flow, oi, greek_col):
    """Gamma zero nearest spot (current-time column) under our parity IV vs the vendor's IV, same positions."""
    import numpy as np
    rows = []
    for t in sorted(viv or {}):
        if t < "09:31":                    # the 09:30 pull still carries the prior close
            continue
        j = spx_flow.get(t)
        if not j or not j.get("x"):
            continue
        S = float(j["x"])
        idx = {float(k): i for i, k in enumerate(j["k"])}
        K, sp, sv, pos = [], [], [], []
        for ks, (cv_, pv_) in viv[t]["k"].items():
            k = float(ks)
            i = idx.get(k)
            o = oi.get("%.1f" % k) or oi.get(ks)
            if i is None or not o:
                continue
            par = (j["ci"][i] if k >= S else j["pi"][i]) or 0
            ven = (cv_ if k >= S else pv_) or 0
            if par <= 0 or ven <= 0:
                continue
            K.append(k); sp.append(par); sv.append(float(ven))
            pos.append((o[0] or 0) + (j["cv"][i] or 0) - (o[1] or 0) - (j["pv"][i] or 0))
        if len(K) < 10:
            continue
        prices = np.arange(S - 100, S + 100.01, 2.5)
        ty = max(960 - mm(t), 1) / 525600.0
        z = []
        for sg in (sp, sv):
            col = greek_col("gamma", np.array(K), np.array(sg), np.array(pos, dtype=float), prices, ty)
            zz = [prices[q] + 2.5 * abs(col[q]) / (abs(col[q]) + abs(col[q + 1])) for q in range(len(col) - 1)
                  if col[q] != 0 and (col[q] > 0) != (col[q + 1] > 0)]
            z.append(min(zz, key=lambda v: abs(v - S)) if zz else None)
        ka = min(range(len(K)), key=lambda q: abs(K[q] - S))
        rows.append({"t": t, "S": S, "gz_parity": z[0], "gz_vendor": z[1], "atm_parity": sp[ka], "atm_vendor": sv[ka]})
    d = [abs(r["gz_parity"] - r["gz_vendor"]) for r in rows if r["gz_parity"] is not None and r["gz_vendor"] is not None]
    iv = [(r["atm_vendor"] - r["atm_parity"]) * 100 for r in rows]
    return {"n": len(rows), "gz_diff_med": _med(d), "gz_diff_max": max(d) if d else None,
            "atm_iv_diff_med": _med(iv), "rows": rows}


# ------------------------------------------------------------------ seq / W-M
def wm_score(wm, path):
    out = []
    pm = dict(path)
    for e in wm or []:
        if e.get("t") is None or e.get("px") is None:
            continue
        d = 1 if e["type"] == "W" else -1
        fut = [x for (m, x) in path if e["t"] < m <= e["t"] + 30]
        if not fut:
            continue
        mv = d * (fut[-1] - e["px"])
        out.append({"type": e["type"], "t": hh(e["t"]), "px": e["px"], "agree": e.get("agree"), "move30": round(mv, 2), "right": mv > 0})
    return out


# ------------------------------------------------------------------ Phase 2: how good are Convex's mid guesses?
def guess_score(prints, quotes):
    """SPX 0DTE prints at MID / INSIDE their quote carry Convex's guessed side. Judge: where that contract's
    mid went over the next 2 minutes (up = buyer pressure). Pre-registered 2026-11-04; |move| < 0.025 = flat."""
    def midq(t, k, cp):
        v = (quotes or {}).get(t, {}).get(str(int(round(k))))
        if not v:
            return None
        b, a = (v[0], v[1]) if cp == "C" else (v[2], v[3])
        return (b + a) / 2 if b is not None and a and a >= b else None
    n = agree = flat = miss = 0
    for p in prints or []:
        m = mm(p["t"])
        m0 = midq(p["t"], p["strike"], p["cp"])
        m2 = next((v for v in (midq(hh(m + q), p["strike"], p["cp"]) for q in (2, 3, 1)) if v is not None), None)
        if m0 is None or m2 is None:
            miss += 1
            continue
        d = m2 - m0
        if abs(d) < 0.025:
            flat += 1
            continue
        n += 1
        agree += 1 if (d > 0) == (p["side"] == "buy") else 0
    return {"n": n, "agree": agree, "flat": flat, "no_quote": miss, "pct": (agree / n) if n else None}


# ------------------------------------------------------------------ assemble + running + HTML
PROJ_VARS = ("plain", "smile", "vclock", "both")


def proj_score(proj, close):
    """Phase 3 gate: projected 15:50 gamma zero / gamma peak vs the 16:00 close, per model, at 10:30/12:00/14:00.
    Error = |level - close|. Naive = |spot at that time - close|. A model is kept only if its error beats plain."""
    out = []
    if not proj or close is None:
        return out
    for t in sorted(proj):
        v = proj[t] or {}
        if v.get("err"):
            continue
        r = {"t": t}
        for name in PROJ_VARS:
            x = v.get(name)
            if not x:
                continue
            if r.get("S") is None and x.get("S"):
                r["S"] = x["S"]
                r["naive"] = abs(x["S"] - close)
            r[name + "_gz"] = x.get("gz")
            r[name + "_gz_err"] = None if x.get("gz") is None else abs(x["gz"] - close)
            r[name + "_pk"] = x.get("gpk")
            r[name + "_pk_err"] = None if x.get("gpk") is None else abs(x["gpk"] - close)
        out.append(r)
    return out


BASIS_VARS = (("oiv", "OI + vol"), ("ois", "OI + signed"), ("led", "Ledger"))


def basis_score(bs, close, path):
    """Charm by basis: per time and basis - colour changes near spot, projected charm flip / gamma zero vs the close,
    and what price did at the charm flip nearest spot at that minute (held / broke / touched / untouched)."""
    out = []
    if not bs or close is None:
        return out
    for t in sorted(bs):
        v = bs[t] or {}
        if v.get("err"):
            continue
        r = {"t": t}
        for name, _ in BASIS_VARS:
            x = v.get(name)
            if not x or x.get("na"):
                continue
            r[name + "_flips"] = x.get("flips")
            r[name + "_cz_err"] = None if x.get("cz") is None else abs(x["cz"] - close)
            r[name + "_gz_err"] = None if x.get("gz") is None else abs(x["gz"] - close)
            if x.get("cz_now") is not None and path:
                r[name + "_cz_now"] = round(x["cz_now"], 1)
                r[name + "_cz_res"] = score_level([(m_, x_) for (m_, x_) in path if m_ >= mm(t)], x["cz_now"]).get("result")
        out.append(r)
    return out


def build(day_iso, path, close, cols, den_runs, cone0, p1js, doc, gs, viv, spx_flow, oi, greek_col, own=None):
    colsf = [c for c in cols or [] if c.get("S")]

    def ours_at(m):
        c = _col_at(colsf, m)
        if not c or not c["gz"]:
            return None
        return min(c["gz"], key=lambda z: abs(z - c["S"]))
    dflip = doc_flip(doc)
    res = {"day": day_iso,
           "pred": predictions(den_runs, colsf, path, close),
           "boundary": boundary(colsf, path),
           "levels": outside_levels(path, doc, gs, cone0, own),
           "doc_grades": (p1js or {}).get("doc") if doc else None,
           "own_grades": (p1js or {}).get("own"),
           "flip": {"ours": flip_tests(path, ours_at),
                    "doc": flip_tests(path, lambda m: dflip) if dflip else None, "doc_level": dflip},
           "gate": gate_crossings(path, ours_at, (p1js or {}).get("gate")),
           "seq": (p1js or {}).get("seq"),
           "wm": wm_score((p1js or {}).get("wm"), path),
           "proj": proj_score((p1js or {}).get("proj"), close),
           "basis": basis_score((p1js or {}).get("basis"), close, path),
           "errors": {k: v for k, v in (p1js or {}).items() if k.endswith("err")}}
    try:
        res["iv"] = iv_variants(viv, spx_flow, oi, greek_col)
    except Exception as exc:
        res["iv"] = {"err": str(exc)[:160]}
    return res


def running(day_dirs):
    """day_dirs: [(day_iso, p1 dict)] -> pooled numbers across nights."""
    R = {"days": [d for d, _ in day_dirs]}
    byh = {}
    for _, p in day_dirs:
        for r in p.get("pred") or []:
            a = byh.setdefault(r["t"], {"med": [], "naive": [], "zone": [], "inside": [], "top": [], "bot": [], "vt": [], "vb": []})
            a["med"].append(r.get("med_err")); a["naive"].append(r.get("naive_err"))
            if "zone_hit" in r:
                a["zone"].append(1 if r["zone_hit"] else 0); a["inside"].append(1 if r["inside"] else 0)
            a["top"].append(r.get("top_err")); a["bot"].append(r.get("bot_err")); a["vt"].append(r.get("vt_err")); a["vb"].append(r.get("vb_err"))
    R["pred"] = {t: {k: _mean([abs(v) if k in ("top", "bot", "vt", "vb") and v is not None else v for v in a[k]]) for k in a} | {"n": len([v for v in a["naive"] if v is not None])}
                 for t, a in sorted(byh.items())}
    bl = {}
    for _, p in day_dirs:
        b = p.get("boundary") or {}
        for name, r in (b.get("lines") or {}).items():
            if r.get("err") is None:
                continue
            k = (name, "binds" if r["rule_says_binds"] else "off-side")
            a = bl.setdefault(k, {"err": [], "contain": []})
            a["err"].append(abs(r["err"])); a["contain"].append(r["contain"])
    R["boundary"] = {"%s|%s" % k: {"n": len(a["err"]), "err": _mean(a["err"]), "contain": _mean(a["contain"])} for k, a in sorted(bl.items())}
    lv = {}
    for _, p in day_dirs:
        for r in p.get("levels") or []:
            a = lv.setdefault(r["src"], {})
            a[r["result"]] = a.get(r["result"], 0) + 1
    R["levels"] = lv
    rm = {}
    for _, p in day_dirs:
        for lab, key in (("Doc", "doc_grades"), ("Ours", "own_grades")):
            G = [g for g in (p.get(key) or []) if g.get("kind") != "lean"]
            if not G:
                continue
            a = rm.setdefault(lab, {"days": 0, "trig": 0, "hit": 0})
            a["days"] += 1
            a["trig"] += sum(1 for g in G if g.get("t0") is not None)
            a["hit"] += sum(1 for g in G if g.get("hit") is not None)
    R["roadmap"] = rm
    fl = {"ours": [], "doc": [], "placebo": []}
    for _, p in day_dirs:
        f = p.get("flip") or {}
        for k in ("ours", "doc"):
            if f.get(k) and f[k]["split"]["ratio"]:
                fl[k].append(f[k]["split"]["ratio"])
                if k == "ours" and f[k].get("placebo_ratio_mean"):
                    fl["placebo"].append(f[k]["placebo_ratio_mean"])
    R["flip"] = {k: {"n": len(v), "mean": _mean(v)} for k, v in fl.items()}
    gh, gl = [], []
    for _, p in day_dirs:
        g = p.get("gate") or {}
        if g.get("hi_ft") is not None:
            gh += [g["hi_ft"]] * g["hi_n"]
        if g.get("lo_ft") is not None:
            gl += [g["lo_ft"]] * g["lo_n"]
    R["gate"] = {"hi_n": len(gh), "hi_ft": _mean(gh), "lo_n": len(gl), "lo_ft": _mean(gl)}
    sq = {}
    for _, p in day_dirs:
        for e in p.get("seq") or []:
            a = sq.setdefault(e["type"], [0, 0, 0])
            if e["res"] == "✓":
                a[0] += 1
            elif e["res"] == "✗":
                a[1] += 1
            else:
                a[2] += 1
    R["seq"] = sq
    wm = {}
    for _, p in day_dirs:
        for e in p.get("wm") or []:
            k = "%s flow-%s" % (e["type"], "agrees" if e.get("agree") else "disagrees")
            a = wm.setdefault(k, [0, 0, []])
            a[0] += 1; a[1] += 1 if e["right"] else 0; a[2].append(e["move30"])
    R["wm"] = {k: {"n": a[0], "right": a[1], "mean": _mean(a[2])} for k, a in wm.items()}
    gn = sum((p.get("guess") or {}).get("n", 0) for _, p in day_dirs)
    ga = sum((p.get("guess") or {}).get("agree", 0) for _, p in day_dirs)
    R["guess"] = {"n": gn, "agree": ga, "pct": (ga / gn) if gn else None}
    pj = {}
    for _, p in day_dirs:
        for r in p.get("proj") or []:
            a = pj.setdefault(r["t"], {"naive": []})
            if r.get("naive") is not None:
                a["naive"].append(r["naive"])
            for name in PROJ_VARS:
                for k in ("gz", "pk"):
                    e = r.get("%s_%s_err" % (name, k))
                    if e is not None:
                        a.setdefault(name + "_" + k, []).append(e)
    R["proj"] = {t: {k: _mean(v) for k, v in a.items()} | {"n": len(a["naive"])} for t, a in sorted(pj.items())}
    bz = {}
    for _, p in day_dirs:
        for r in p.get("basis") or []:
            for name, _l in BASIS_VARS:
                if r.get(name + "_flips") is None:
                    continue
                a = bz.setdefault(name, {"n": 0, "flips": [], "cz": [], "gz": [], "held": 0, "broke": 0, "tested": 0})
                a["n"] += 1
                a["flips"].append(r[name + "_flips"]); a["cz"].append(r.get(name + "_cz_err")); a["gz"].append(r.get(name + "_gz_err"))
                res = r.get(name + "_cz_res")
                if res in ("held", "broke", "touched"):
                    a["tested"] += 1
                    a["held"] += 1 if res == "held" else 0
                    a["broke"] += 1 if res == "broke" else 0
    R["basis"] = {k: {"n": a["n"], "flips": _mean(a["flips"]), "cz": _mean(a["cz"]), "gz": _mean(a["gz"]),
                      "held": a["held"], "broke": a["broke"], "tested": a["tested"]} for k, a in bz.items()}
    return R


def _f(x, d=1):
    return "–" if x is None else ("{:,.%df}" % d).format(x)


def _tbl(head, rows):
    h = ["<div class='wrap'><table><tr>%s</tr>" % "".join("<th>%s</th>" % c for c in head)]
    for r in rows:
        h.append("<tr>%s</tr>" % "".join("<td>%s</td>" % c for c in r))
    h.append("</table></div>")
    return "".join(h)


def html(p, R):
    h = []
    # predictions
    h.append("<h2>Predictions by hour (#15)</h2><div class='sub'>Density cone run at each hour vs the actual close and the rest-of-day high/low. "
             "Beats naive = median error smaller than \"close = spot at that hour\". Top/bottom errors: + = predicted level above the actual extreme.</div>")
    h.append(_tbl(["hour", "spot", "cone median", "err", "naive err", "close in 5-pt zone", "in P10–P90", "P90 − high", "P10 − low", "vanna top − high", "vanna bottom − low"],
                  [[r["t"], _f(r["spot"]), _f(r.get("med")), _f(r.get("med_err")), _f(r["naive_err"]), "yes" if r.get("zone_hit") else "no",
                    "yes" if r.get("inside") else "no", _f(r.get("top_err")), _f(r.get("bot_err")), _f(r.get("vt_err")), _f(r.get("vb_err"))] for r in p["pred"]]))
    if R.get("pred"):
        h.append("<details open><summary>Running, %d nights</summary>%s</details>" % (len(R["days"]), _tbl(
            ["hour", "n", "cone err", "naive err", "zone hit", "in P10–P90", "|P90−high|", "|P10−low|", "|vanna top−high|", "|vanna bottom−low|"],
            [[t, a["n"], _f(a["med"]), _f(a["naive"]), _f(a["zone"] and a["zone"] * 100, 0) + "%", _f(a["inside"] and a["inside"] * 100, 0) + "%",
              _f(a["top"]), _f(a["bot"]), _f(a["vt"]), _f(a["vb"])] for t, a in R["pred"].items()])))
    # boundary
    b = p.get("boundary")
    if b:
        h.append("<h2>Boundary rule</h2><div class='sub'>Your rule: long gamma → vanna caps the top, gamma floors the bottom; short gamma → the opposite. "
                 "Today's regime: <b>%s gamma</b> (%s of columns long). Level locks when 10 straight 2-min columns sit within ±2 pts. "
                 "Error = level minus the rest-of-day extreme; small error on the 'binds' lines = the rule worked.</div>"
                 % (b["regime"] or "?", _f((b.get("pos_share") or 0) * 100, 0) + "%"))
        h.append(_tbl(["line", "rule says", "locked", "level", "error", "contained"],
                      [[k.replace("_", " "), "binds" if r["rule_says_binds"] else "off-side", r["lock"] or "never", _f(r.get("level")), _f(r.get("err")),
                        _f(r.get("contain"), 0) + "%" if r.get("contain") is not None else "–"] for k, r in b["lines"].items()]))
        if R.get("boundary"):
            h.append("<details><summary>Running</summary>%s</details>" % _tbl(["line | rule", "n", "mean |error|", "mean contained"],
                     [[k, a["n"], _f(a["err"]), _f(a["contain"], 0) + "%"] for k, a in R["boundary"].items()]))
    # levels
    h.append("<h2>Doc's and outside levels (#17)</h2><div class='sub'>touch = within 2 pts · held = went 10 pts back the way it came · broke = 3 one-minute closes 5+ pts through.</div>")
    h.append(_tbl(["source", "level", "value", "touch", "result", "at"],
                  [[r["src"], r["name"], _f(r["level"]), r.get("touch", "–"), r["result"], r.get("t", "")] for r in p["levels"]]))
    if R.get("levels"):
        h.append("<details><summary>Running by source</summary>%s</details>" % _tbl(["source", "held", "broke", "touched only", "untouched", "gapped"],
                 [[s, a.get("held", 0), a.get("broke", 0), a.get("touched", 0), a.get("untouched", 0), a.get("gapped through", 0)] for s, a in R["levels"].items()]))
    if p.get("doc_grades"):
        h.append("<h3>Doc's road map (the page's own grading)</h3>")
        h.append(_tbl(["IF / THEN", "state", "triggered", "target hit"],
                      [[g["text"], g["state"], hh(g["t0"]) if g.get("t0") else "–", hh(g["hit"]) if g.get("hit") else "–"] for g in p["doc_grades"]]))
    if p.get("own_grades"):
        h.append("<h3>Our road map (built 08:35 from our dealer book, graded the same way)</h3>")
        h.append(_tbl(["IF / THEN", "state", "triggered", "target hit"],
                      [[g["text"], g["state"], hh(g["t0"]) if g.get("t0") else "–", hh(g["hit"]) if g.get("hit") else "–"] for g in p["own_grades"]]))
    if R.get("roadmap"):
        h.append("<div class='sub'>Road map, running: a line 'triggered' when its level converted; 'hit' when the target followed. Higher hit-of-triggered = the better map.</div>")
        h.append(_tbl(["map", "days", "lines triggered", "targets hit", "hit / triggered"],
                      [[k, v["days"], v["trig"], v["hit"], ("%.0f%%" % (100.0 * v["hit"] / v["trig"])) if v["trig"] else "–"] for k, v in R["roadmap"].items()]))
    # flip
    f = p["flip"]
    h.append("<h2>Flip scorecard (#85)</h2><div class='sub'>Vol split = average 1-min move below the line ÷ above it. Over 1 = the line separates calm (above) from fast (below), as a real flip should. "
             "Placebo = the same number for fake lines 25 and 50 pts away; the real line should beat it. Doc's flip = his published pivot (%s).</div>" % _f(f.get("doc_level"), 0))
    rows = []
    for k, lab in (("ours", "ours (page gamma zero)"), ("doc", "Doc's pivot")):
        x = f.get(k)
        if x:
            s = x["split"]
            rows.append([lab, _f(s["ratio"], 2), _f(x.get("placebo_ratio_mean"), 2) if k == "ours" else "–", "%d / %d" % (s["n_above"], s["n_below"]),
                         "%d · %s" % (x["cross_down_n"], _f(x["cross_down_rng30"])), "%d · %s" % (x["cross_up_n"], _f(x["cross_up_rng30"])), _f(x["any_rng30"])])
    h.append(_tbl(["line", "vol split", "placebo", "min above / below", "crosses down · 30-min range", "crosses up · 30-min range", "any 30-min range"], rows))
    if R.get("flip"):
        h.append("<div class='sub'>Running vol split: ours %s (n %d) · Doc %s (n %d) · placebo %s</div>" % (
            _f(R["flip"]["ours"]["mean"], 2), R["flip"]["ours"]["n"], _f(R["flip"]["doc"]["mean"], 2), R["flip"]["doc"]["n"], _f(R["flip"]["placebo"]["mean"], 2)))
    # gate + IV
    g = p.get("gate") or {}
    iv = p.get("iv") or {}
    h.append("<h2>Gate and IV check (#16)</h2><div class='sub'>Net ÷ gross gamma at spot (1 = everyone on one side, 0 = balanced book). Low = map levels not load-bearing. "
             "Crossings of our flip, split by the day's median gate: follow-through = points moved in the cross direction over 15 min.</div>")
    h.append(_tbl(["item", "value"], [
        ["gate (all expiries): open / median / close", "%s / %s / %s" % (_f(g.get("gate_open"), 3), _f(g.get("gate_median"), 3), _f(g.get("gate_close"), 3))],
        ["gate (0DTE only) median", _f(g.get("r0_median"), 3)],
        ["crossings with gate ≥ median: n · follow-through", "%s · %s" % (g.get("hi_n", 0), _f(g.get("hi_ft")))],
        ["crossings with gate < median: n · follow-through", "%s · %s" % (g.get("lo_n", 0), _f(g.get("lo_ft")))],
        ["running: high-gate / low-gate follow-through", "%s (n %d) / %s (n %d)" % (_f(R.get("gate", {}).get("hi_ft")), R.get("gate", {}).get("hi_n", 0), _f(R.get("gate", {}).get("lo_ft")), R.get("gate", {}).get("lo_n", 0))],
        ["vendor IV vs our parity IV: ATM gap (vol pts, median)", _f(iv.get("atm_iv_diff_med"), 2)],
        ["gamma zero moved by the IV choice: median / max (pts)", "%s / %s (%s samples)" % (_f(iv.get("gz_diff_med")), _f(iv.get("gz_diff_max")), iv.get("n", 0))]]))
    # Phase 3 map models
    if p.get("proj"):
        h.append("<h2>Map projection by model (Phase 3)</h2><div class='sub'>At 10:30, 12:00 and 14:00 the map is projected to the close four ways: "
                 "plain (today's live map), Smile (smile moves with spot), Var clock (busy open/close, quiet lunch), and both. "
                 "Numbers = points between the projected 15:50 level and the 16:00 close (smaller is better). gz = gamma zero nearest spot, pk = biggest gamma peak. "
                 "A button graduates only if it beats plain over many nights.</div>")
        cols_ = ["time", "spot", "naive"] + ["%s %s" % (n, k) for n in PROJ_VARS for k in ("gz", "pk")]
        h.append(_tbl(cols_, [[r["t"], _f(r.get("S")), _f(r.get("naive"))] + [_f(r.get("%s_%s_err" % (n, k))) for n in PROJ_VARS for k in ("gz", "pk")]
                              for r in p["proj"]]))
        if R.get("proj"):
            h.append("<details open><summary>Running, mean error (pts)</summary>%s</details>" % _tbl(
                ["time", "n", "naive"] + ["%s %s" % (n, k) for n in PROJ_VARS for k in ("gz", "pk")],
                [[t, a["n"], _f(a.get("naive"))] + [_f(a.get("%s_%s" % (n, k))) for n in PROJ_VARS for k in ("gz", "pk")] for t, a in R["proj"].items()]))
    # charm by basis
    if p.get("basis"):
        h.append("<h2>Charm by basis</h2><div class='sub'>The 0DTE charm map built three ways: OI + vol (today's map), OI + signed (Convex's buy/sell on top of OI) "
                 "and Ledger (positions measured since 10/26 plus today's signed flow). Changes = how many times the charm colour flips within ±50 pts of spot "
                 "(more = striped, like OptionsDepth; 1–2 = two slabs). Flip / zero error = points between the projected 15:50 charm flip or gamma zero and the close. "
                 "At flip = what price did at the charm flip nearest spot at that time. A basis graduates only if it beats OI + vol over 10+ nights.</div>")
        head = ["time"] + ["%s %s" % (lab, k) for _, lab in BASIS_VARS for k in ("changes", "flip err", "zero err", "at flip")]
        h.append(_tbl(head, [[r["t"]] + sum([[_f(r.get(n + "_flips"), 0), _f(r.get(n + "_cz_err")), _f(r.get(n + "_gz_err")),
                                             r.get(n + "_cz_res") or "–"] for n, _ in BASIS_VARS], []) for r in p["basis"]]))
        if R.get("basis"):
            h.append("<details open><summary>Running</summary>%s</details>" % _tbl(
                ["basis", "samples", "mean changes", "mean flip err", "mean zero err", "flip held / broke of tested"],
                [[lab, R["basis"][n]["n"], _f(R["basis"][n]["flips"]), _f(R["basis"][n]["cz"]), _f(R["basis"][n]["gz"]),
                  "%d / %d of %d" % (R["basis"][n]["held"], R["basis"][n]["broke"], R["basis"][n]["tested"])] for n, lab in BASIS_VARS if n in R["basis"]]))
    # seq / wm
    h.append("<h2>Sequence bubbles and W/M arrows</h2><div class='sub'>Bubbles: the page's own ✓/✗ (its own target and time limit). W/M: right = price moved the arrow's way over the next 30 min.</div>")
    sq = {}
    for e in p.get("seq") or []:
        a = sq.setdefault(e["type"], [0, 0, 0])
        a[0 if e["res"] == "✓" else 1 if e["res"] == "✗" else 2] += 1
    rows = [["bubbles " + k, "%d ✓ / %d ✗ / %d open" % tuple(a), "%d ✓ / %d ✗" % tuple(R["seq"].get(k, [0, 0, 0])[:2])] for k, a in sq.items()]
    wmt = {}
    for e in p.get("wm") or []:
        k = "%s flow-%s" % (e["type"], "agrees" if e.get("agree") else "disagrees")
        a = wmt.setdefault(k, [0, 0])
        a[0] += 1; a[1] += 1 if e["right"] else 0
    rows += [[k, "%d of %d right" % (a[1], a[0]), "%d of %d right · mean %s pts" % (R["wm"].get(k, {}).get("right", 0), R["wm"].get(k, {}).get("n", 0), _f(R["wm"].get(k, {}).get("mean")))]
             for k, a in wmt.items()]
    h.append(_tbl(["signal", "today", "running"], rows or [["none today", "–", "–"]]))
    g2 = p.get("guess")
    if g2 is not None:
        h.append("<h2>Convex's guesses on mid trades</h2><div class='sub'>SPX 0DTE prints that traded in the middle of the quote: Convex still calls them buy or sell, "
                 "and the live cockpit uses that call. Judge: did the contract's mid move the guessed way over the next 2 minutes? 50% = coin flip.</div>")
        h.append(_tbl(["", "prints judged", "guess right", "flat (no move)", "no quote"],
                      [["today", g2.get("n", 0), ("%d (%s)" % (g2.get("agree", 0), _f((g2.get("pct") or 0) * 100, 0) + "%")) if g2.get("n") else "–", g2.get("flat", 0), g2.get("no_quote", 0)],
                       ["running", (R.get("guess") or {}).get("n", 0), _f(((R.get("guess") or {}).get("pct") or 0) * 100, 0) + "%" if (R.get("guess") or {}).get("n") else "–", "", ""]]))
    if p.get("errors"):
        h.append("<div class='sub'>page errors: %s</div>" % "; ".join("%s %s" % kv for kv in p["errors"].items()))
    return "".join(h)
