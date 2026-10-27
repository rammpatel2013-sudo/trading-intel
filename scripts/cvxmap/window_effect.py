"""
What do the extra strikes change?

Extracts map levels from the SAME Convex book twice:
  clipped = centre +- 20*step  (41 strikes, exactly what ToS can see)
  full    = everything Convex returns (~108 strikes)

Levels are read the way the map is read: greekCol (the cockpit's own JS
formula, ported verbatim), on the OI+volume basis, at the 16:15 edge of the
map, at T-10 (the last smooth column - the literal final column has ty floored
to 1 minute and combs).

A sign flip counts only if |v| reaches 0.25*vmax within 4 rows on BOTH sides -
the same amplitude gate the heat map applies via |v/vmax|^0.6.
"""
import json, math, gzip, glob, sys, os
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cvx_live as L

INV_SQ2PI = 1.0 / math.sqrt(2.0 * math.pi)
T_MINUS = 10          # read at T-10, not the floored final column
GRID_HALF = 160.0
GRID_STEP = 2.5          # the map's own row spacing (the *2.5 in greekCol)
GATE_FRAC = 0.25
GATE_ROWS = 4


def greek_col(kind, k, sg, pos, prices, ty):
    """Verbatim port of the cockpit's greekCol()."""
    sq = math.sqrt(max(ty, 1e-7))
    S = prices[:, None]
    d1 = (np.log(S / k[None, :]) + 0.5 * sg[None, :] ** 2 * ty) / (sg[None, :] * sq)
    d2 = d1 - sg[None, :] * sq
    p = np.exp(-d1 * d1 / 2) * INV_SQ2PI
    if kind == "gamma":
        c = pos[None, :] * p / (S * sg[None, :] * sq) * 2.5
    elif kind == "charm":
        c = -pos[None, :] * (p * d2 / (2 * ty)) * (5 / 525600)
    else:
        c = -pos[None, :] * (-p * d2 / sg[None, :]) * 0.01
    return c.sum(axis=1)


def transitions(prices, col):
    """Sign flips that are actually visible on the heat map."""
    vmax = np.max(np.abs(col))
    if vmax <= 0:
        return []
    gate = GATE_FRAC * vmax
    out = []
    for i in range(len(col) - 1):
        a, b = col[i], col[i + 1]
        if a == 0 or (a > 0) == (b > 0):
            continue
        lo = max(0, i - GATE_ROWS); hi = min(len(col), i + GATE_ROWS + 2)
        if np.max(np.abs(col[lo:i + 1])) < gate:    continue
        if np.max(np.abs(col[i + 1:hi])) < gate:    continue
        x = prices[i] + (prices[i + 1] - prices[i]) * abs(a) / (abs(a) + abs(b))
        out.append(round(float(x), 2))
    return out


def book_from(dec, sym, clip_step=None):
    d = dec[sym]; spot = d["spot"]; step = L.STEP[sym]
    _, book0 = d["exps"][0]
    centre = round(spot / step) * step
    ks = sorted(book0)
    if clip_step:
        ks = [k for k in ks if abs(k - centre) <= clip_step * step]
    F, _ = L.parity_forward([(k, c, p) for k, (c, p) in book0.items()], spot)
    now = L.now_et()
    tiv = max(L.EXPIRY_MIN - (now.hour * 60 + now.minute), 1) / 525600.0
    K, SG, POS = [], [], []
    for k in ks:
        c, p = book0[k]
        civ = L.implied(L.mid(c), F, k, tiv, "C")
        piv = L.implied(L.mid(p), F, k, tiv, "P")
        sg = (civ if k >= spot else piv) or (piv if k >= spot else civ)
        if not sg or sg <= 0:
            continue
        cp = L._n(c.get("oi")) + L._n(c.get("volm"))      # oiv basis
        pp = L._n(p.get("oi")) + L._n(p.get("volm"))
        if cp == 0 and pp == 0:
            continue
        K.append(k); SG.append(sg); POS.append(cp - pp)    # naive, as the map does
    return spot, np.array(K), np.array(SG), np.array(POS)


def main():
    sym = sys.argv[1].upper() if len(sys.argv) > 1 else "SPX"
    day = L.now_et().strftime("%Y%m%d")
    raw = os.path.join("data", "raw_%s.ndjson.gz" % day)
    rec = None
    for line in gzip.open(raw, "rt", encoding="utf-8"):
        r = json.loads(line)
        if r.get("pull") == "idx":
            rec = r
    dec = L.decode(rec)
    ty = T_MINUS / 525600.0
    print("%s  %s   read at T-%d (ty=%.3g), OI+vol basis, naive sign\n" % (sym, rec["ts"], T_MINUS, ty))

    res = {}
    for label, clip in (("clipped (ToS +-20 steps)", L.HALF_WIN), ("full (all Convex strikes)", None)):
        spot, K, SG, POS = book_from(dec, sym, clip)
        prices = np.arange(spot - GRID_HALF, spot + GRID_HALF + GRID_STEP, GRID_STEP)
        row = {"n": len(K), "lo": K.min(), "hi": K.max(), "spot": spot}
        for kind in ("gamma", "vanna", "charm"):
            col = greek_col(kind, K, SG, POS, prices, ty)
            row[kind] = transitions(prices, col)
        res[label] = row
        print("%-28s %3d strikes  %.0f-%.0f" % (label, row["n"], row["lo"], row["hi"]))
        for kind in ("gamma", "vanna", "charm"):
            t = row[kind]
            print("    %-6s %s" % (kind, ", ".join("%.1f" % x for x in t) if t else "(none visible)"))
        print()

    a, b = res["clipped (ToS +-20 steps)"], res["full (all Convex strikes)"]
    print("=== what the extra strikes change ===")
    for kind in ("gamma", "vanna", "charm"):
        ta, tb = a[kind], b[kind]
        if ta and tb:
            pairs = [(x, min(tb, key=lambda y: abs(y - x))) for x in ta]
            moves = [abs(x - y) for x, y in pairs]
            print("  %-6s clipped %d -> full %d levels;  nearest-match shift: max %.1f, mean %.1f pts"
                  % (kind, len(ta), len(tb), max(moves), sum(moves) / len(moves)))
            for x, y in pairs:
                if abs(x - y) >= 0.5:
                    print("        %.1f -> %.1f  (%+.1f)" % (x, y, y - x))
            new = [y for y in tb if all(abs(y - x) > 2.0 for x in ta)]
            if new:
                print("        levels only visible at full width: %s" % ", ".join("%.1f" % v for v in new))
        else:
            print("  %-6s clipped=%s  full=%s" % (kind, ta or "none", tb or "none"))


if __name__ == "__main__":
    main()
