"""
Score the close prediction, ToS feed vs Convex feed, minute by minute.

The predictor (validated 2026-10-26): the GAMMA ZERO on the contour at the
16:15 edge of the map, read at T-10, on the OI+volume basis with the naive sign.

For each minute t we rebuild the dealer book exactly as mapRowsAt() does:
    sg = otmIV(k, x_t, ci_t, pi_t)        per-strike OTM IV from that minute
    cp = cOI_live + cv_t                  OI from the LIVE book (OI is daily),
    pp = pOI_live + pv_t                  volume from that minute's snapshot
then project it to the close with ty = 10 minutes and find the gamma sign flip.

Usage:
    python close_score.py                 # both feeds, scores if the close is known
    python close_score.py --close 7735.2  # supply the actual close explicitly
"""
import json, os, sys, math, glob
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cvx_live as L
import window_effect as W

TY = 10 / 525600.0


def otm_iv(k, S, civ, piv):
    a = civ if k >= S else piv
    b = piv if k >= S else civ
    return a or b or 0.0


def live_oi(live_path, sym):
    d = json.load(open(live_path, encoding="utf-8"))[sym]
    return {r["k"]: (r["cOI"], r["pOI"]) for r in d["rows"]}, d


def predict(flow_rows, oi, clip=None, centre=None, step=5.0):
    """One minute -> predicted close (gamma zero), or None."""
    K, SG, POS = [], [], []
    S = flow_rows["x"]
    for i, k in enumerate(flow_rows["k"]):
        if clip and centre is not None and abs(k - centre) > clip * step:
            continue
        sg = otm_iv(k, S, flow_rows["ci"][i], flow_rows["pi"][i])
        if not sg or sg <= 0:
            continue
        c_oi, p_oi = oi.get(k, (0.0, 0.0))
        cp = c_oi + (flow_rows["cv"][i] or 0)
        pp = p_oi + (flow_rows["pv"][i] or 0)
        if cp == 0 and pp == 0:
            continue
        K.append(k); SG.append(sg); POS.append(cp - pp)
    if len(K) < 8:
        return None, S
    prices = np.arange(S - W.GRID_HALF, S + W.GRID_HALF + W.GRID_STEP, W.GRID_STEP)
    col = W.greek_col("gamma", np.array(K), np.array(SG), np.array(POS), prices, TY)
    t = W.transitions(prices, col)
    if not t:
        return None, S
    return min(t, key=lambda x: abs(x - S)), S


def load_flow(path, sym):
    out = {}
    if not os.path.exists(path):
        return out
    for line in open(path, encoding="utf-8"):
        try:
            j = json.loads(line)
        except Exception:
            continue
        if j.get("s") != sym:
            continue
        out[j["t"]] = j
    return out


def main():
    sym = "SPX"
    close = None
    if "--close" in sys.argv:
        close = float(sys.argv[sys.argv.index("--close") + 1])
    day = L.now_et().strftime("%Y%m%d")
    root = os.path.dirname(os.path.abspath(__file__))
    par = os.path.dirname(root)

    tos_flow = load_flow(os.path.join(par, "flow_%s.ndjson" % day), sym)
    cvx_flow = load_flow(os.path.join(root, "data", "flow_%s.ndjson" % day), sym)
    oi_t, dt = live_oi(os.path.join(par, "live.json"), sym)
    oi_c, dc = live_oi(os.path.join(root, "data", "live.json"), sym)
    step = L.STEP[sym]
    centre = round(dc["spot"] / step) * step

    if close is None:
        close = dc["spot"]
        note = "(close not in yet - scoring against CURRENT spot %.2f)" % close
    else:
        note = "(actual close %.2f)" % close

    print("%s  %s" % (sym, note))
    print("ToS flow minutes=%d   Convex flow minutes=%d   common=%d"
          % (len(tos_flow), len(cvx_flow), len(set(tos_flow) & set(cvx_flow))))
    print()
    rows = []
    for t in sorted(set(tos_flow) | set(cvx_flow)):
        if t < "09:30" or t > "16:15":
            continue
        pt = ps = pc = None
        if t in tos_flow:
            pt, _ = predict(tos_flow[t], oi_t)
        if t in cvx_flow:
            pc, _ = predict(cvx_flow[t], oi_c)
            ps, _ = predict(cvx_flow[t], oi_c, clip=L.HALF_WIN, centre=centre, step=step)
        rows.append((t, pt, ps, pc))

    def err(v):
        return None if v is None else abs(v - close)

    print("  time    ToS(41)   Cvx-clip   Cvx-full  |  errToS  errClip  errFull")
    for t, pt, ps, pc in rows:
        if t[-1] not in "05":           # print every 5 minutes
            continue
        f = lambda v: ("%8.1f" % v) if v is not None else "       -"
        g = lambda v: ("%7.2f" % err(v)) if v is not None else "      -"
        print("  %s %s %s %s  | %s %s %s" % (t, f(pt), f(ps), f(pc), g(pt), g(ps), g(pc)))

    print()
    for lbl, idx in (("ToS (41 strikes)", 1), ("Convex clipped (41)", 2), ("Convex full (108)", 3)):
        for lo, hi, win in (("09:30", "16:15", "all day"), ("12:00", "16:15", "after 12:00"),
                            ("15:00", "16:15", "after 15:00")):
            es = [err(r[idx]) for r in rows if lo <= r[0] <= hi and r[idx] is not None]
            if not es:
                continue
            es_sorted = sorted(es)
            print("  %-20s %-12s n=%3d  mean=%6.2f  median=%6.2f  p90=%6.2f"
                  % (lbl, win, len(es), sum(es) / len(es), es_sorted[len(es_sorted) // 2],
                     es_sorted[int(len(es_sorted) * 0.9)]))
        print()


if __name__ == "__main__":
    main()
