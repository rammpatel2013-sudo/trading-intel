"""
Convex cockpit collector - STAGE 2: raw pulls -> cockpit live.json + flow ndjson.
Created 2026-10-27.

Reads _cvxmap\\data\\raw_YYYYMMDD.ndjson.gz (written by cvx_collect.py) and emits
cockpit-schema files into _cvxmap\\data\\. Deliberately a SEPARATE process from
stage 1 so it can be fixed and re-run over the whole day without ever risking
the banking.

REPLICATION CONTRACT (locked with Mithil 2026-10-27):
  * rows[] reproduces the ToS formulas EXACTLY, including the naive (cOI-pOI)
    and (cV-pV) signing. Calculations are not changed.
  * Measured/real signing is added as SEPARATE fields (xrows[]) that the v1 map
    ignores entirely.
  * Both IV variants are carried (parity-forward and vendor) so the map can be
    rendered either way and judged on evidence.

Exact ToS model constants, read out of the workbook 2026-10-27:
  r       = Setup!B37 = 0.04
  minT    = Setup!B38 = 0.25 DAYS  -> T = MAX(DTE, 0.25)/365, so on a 0DTE day
            T is pinned at 6 hours ALL SESSION and the greeks never decay.
  q       = Setup!C39 = 0
  sigma   = Setup!B42 = "ATM IV" -> ONE flat vol (the CALL IV at the centre
            strike) is used for EVERY strike. There is no skew in the greeks.
  window  = centre +- 20*step  (41 strikes), centre = round(spot/step)*step

Run:  python cvx_live.py            (loop, follows the raw file)
      python cvx_live.py --once     (single pass)
      python cvx_live.py --rebuild  (reprocess the whole day from line 0)
"""
import os, sys, json, math, gzip, time, datetime, traceback

# VIX tab data (2026-10-28): forward/ATM per expiry, implied variance, regime
# board, events. Optional - if it fails to import, the writer runs as before.
try:
    import vixdash
except Exception:
    vixdash = None

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

# Params the chain endpoint accepts but never returns per contract. Requesting
# them shortens every row, so alignment must skip them. See cvx_collect.py.
DROPPED = {"flownet","vflownet","flowratio","vflowratio",
           "prop1","prop2","prop3","prop4",
           "forward_price","interest","dividend"}

R_RATE     = 0.04      # Setup!B37
MIN_T_DAYS = 0.25      # Setup!B38
Q_YIELD    = 0.0       # Setup!C39
STEP       = {"SPX": 5.0, "SPY": 1.0, "QQQ": 1.0, "VIX": 0.5}
HALF_WIN   = 20        # +-20 steps -> 41 strikes, exactly ToS
B          = 1e9       # ToS divides exposures by 1e9
SQ2PI      = math.sqrt(2.0 * math.pi)

# Reason in Eastern, never host-local. Stage 1 learned this the hard way: on a
# UTC host, naive now() drove minutes_to_close negative -> T floored to 1 minute
# -> IV inflated to ~120%. The laptop is ET but NAS containers are UTC.
try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:
    _ET = None


def now_et():
    if _ET is not None:
        return datetime.datetime.now(_ET).replace(tzinfo=None)
    return datetime.datetime.utcnow() - datetime.timedelta(hours=4)


def today_et():
    return now_et().date()


def log(msg):
    stamp = now_et().strftime("%Y-%m-%d %H:%M:%S")
    line = "%s  %s" % (stamp, msg)
    print(line, flush=True)
    try:
        os.makedirs(DATA, exist_ok=True)
        with open(os.path.join(DATA, "stage2_%s.log" % now_et().strftime("%Y%m%d")),
                  "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def _n(x, d=0.0):
    return d if x is None else float(x)


def ncdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


# ---------- parity forward + Black-76 IV (our IV variant) ----------
def parity_forward(pairs, spot, band=60.0):
    """F = K + midC - midP, median over near-the-money strikes."""
    f = []
    for k, c, p in pairs:
        mc, mp = mid(c), mid(p)
        if mc is None or mp is None or abs(k - spot) > band:
            continue
        f.append(k + mc - mp)
    if not f:
        return spot, 0.0
    f.sort()
    n = len(f)
    med = f[n // 2] if n % 2 else (f[n // 2 - 1] + f[n // 2]) / 2.0
    sd = (sum((x - med) ** 2 for x in f) / n) ** 0.5
    return med, sd


def mid(d):
    b, a = d.get("bid_price"), d.get("ask_price")
    if b is None or a is None or float(a) <= 0:
        return None
    return (float(b) + float(a)) / 2.0


def bs76(F, K, T, sig, kind):
    if sig <= 0 or T <= 0:
        return max(0.0, (F - K) if kind == "C" else (K - F))
    sq = sig * math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * sig * sig * T) / sq
    d2 = d1 - sq
    return (F * ncdf(d1) - K * ncdf(d2)) if kind == "C" else (K * ncdf(-d2) - F * ncdf(-d1))


def implied(px, F, K, T, kind):
    if px is None or px <= 0 or T <= 0 or F <= 0 or K <= 0:
        return None
    intr = max(0.0, (F - K) if kind == "C" else (K - F))
    if px <= intr + 1e-9:
        return None
    lo, hi = 1e-4, 5.0
    for _ in range(60):
        m = (lo + hi) / 2.0
        if bs76(F, K, T, m, kind) < px:
            lo = m
        else:
            hi = m
    v = (lo + hi) / 2.0
    return v if 1e-3 < v < 4.9 else None


# ---------- raw record -> {strike: (call, put)} ----------
def decode(rec):
    """Return {symbol: {"spot":float, "exps":[(exp_head, {k:(c,p)}), ...]}}."""
    eff = [p for p in rec["params"] if p not in DROPPED]
    out = {}
    for sym, entry in zip(rec["symbols"], rec.get("data") or []):
        exps = []
        for grp in entry.get("chain") or []:
            head, rows = grp[0], grp[1]
            book = {}
            for row in rows:
                k = float(row[0])
                c = dict(zip(eff, row[1][1:]))
                p = dict(zip(eff, row[2][1:]))
                c["_sym"], p["_sym"] = row[1][0], row[2][0]
                book[k] = (c, p)
            exps.append((head, book))
        out[sym] = {"spot": _n(entry.get("spot")), "exps": exps}
    return out


# MEASURED 2026-10-27: sweeping the assumed expiry time against ToS's own cIV/pIV
# over 40 strikes, mean IV bias was +0.89 vol pts at 16:00 and -0.04 at 16:15.
# ToS prices SPX 0DTE to the 16:15 SETTLEMENT PRINT, not the 16:00 cash close.
EXPIRY_MIN = 16 * 60 + 15


def minutes_to_close(now=None):
    """Minutes until the 16:15 ET settlement, floored at 1 (as the map's ty is)."""
    now = now or now_et()
    return max(EXPIRY_MIN - (now.hour * 60 + now.minute), 1)


def t_iv(now=None):
    """TRUE time to expiry in years, for IV inversion.

    ToS's cIV/pIV are RTD IMPL_VOL, which uses real time remaining - NOT the
    MAX(DTE,0.25)/365 floor in Chain!R1. That floor governs only the Chain
    sheet's greek columns. Conflating the two depresses IV by sqrt(T_floor/T_true)
    (measured: ~6 vol pts low at 13:30).
    """
    return minutes_to_close(now) / 525600.0


def window_strikes(spot, step):
    centre = round(spot / step) * step
    return [centre + step * i for i in range(-HALF_WIN, HALF_WIN + 1)]


def tos_rows(sym, spot, book, T, sigma, iv_map):
    """rows[] exactly as ChainRows() builds it, over the +-20-step window."""
    step = STEP.get(sym, 1.0)
    sq = sigma * math.sqrt(T) if (sigma > 0 and T > 0) else 0.0
    eqT = math.exp(-Q_YIELD * T)
    rows, xrows = [], []
    for k in window_strikes(spot, step):
        cp = book.get(k)
        if cp is None:
            continue
        c, p = cp
        cOI, pOI = _n(c.get("oi")), _n(p.get("oi"))
        cV, pV = _n(c.get("volm")), _n(p.get("volm"))
        cm, pm = mid(c), mid(p)
        cm = 0.0 if cm is None else cm
        pm = 0.0 if pm is None else pm

        if sq > 0 and k > 0 and spot > 0:
            d1 = (math.log(spot / k) + (R_RATE - Q_YIELD + sigma * sigma / 2.0) * T) / sq
            d2 = d1 - sq
            npd1 = math.exp(-d1 * d1 / 2.0) / SQ2PI
            gamma_u = eqT * npd1 / (spot * sigma * math.sqrt(T))
            vanna_u = -eqT * npd1 * d2 / sigma
            # with q=0 charmC == charmP, so charm also reduces to (cOI-pOI)
            charm_c = (Q_YIELD * eqT * ncdf(d1)
                       - eqT * npd1 * (2 * (R_RATE - Q_YIELD) * T - d2 * sq) / (2 * T * sq))
            charm_p = (-Q_YIELD * eqT * ncdf(-d1)
                       - eqT * npd1 * (2 * (R_RATE - Q_YIELD) * T - d2 * sq) / (2 * T * sq))
        else:
            d1 = d2 = npd1 = gamma_u = vanna_u = charm_c = charm_p = 0.0

        gex = gamma_u * (cOI - pOI) * 100.0 * spot * spot * 0.01 / B          # V = T+U
        dex = (_n(c.get("delta")) * cOI + _n(p.get("delta")) * pOI) * 100.0 * spot / B
        charm = (charm_c * cOI - charm_p * pOI) * 100.0 * spot / 365.0 / B
        vanna = vanna_u * (cOI - pOI) * 100.0 * spot * 0.01 / B
        gexV = gamma_u * 100.0 * spot * spot * 0.01 * (cV - pV) / B

        civ, piv = iv_map.get((k, "C")), iv_map.get((k, "P"))
        rows.append({
            "k": k,
            "gex": gex, "dex": dex, "charm": charm, "vanna": vanna,
            "cV": cV, "pV": pV,
            "cPrem": cV * cm * 100.0 / 1e6, "pPrem": pV * pm * 100.0 / 1e6,
            "cIV": civ or 0, "pIV": piv or 0,
            "gexV": gexV,
            "cOI": cOI, "pOI": pOI,
            "cD": _n(c.get("delta")), "pD": _n(p.get("delta")),
            "cM": cm, "pM": pm,
            "cL": _n(c.get("price")), "pL": _n(p.get("price")),
            "cB": _n(c.get("bid_price")), "cA": _n(c.get("ask_price")), "cZ": _n(c.get("size")),
            "pB": _n(p.get("bid_price")), "pA": _n(p.get("ask_price")), "pZ": _n(p.get("size")),
        })
        # --- extras the v1 map ignores: measured signing + vendor exposures ---
        cgb, cgs = _n(c.get("gammas_buy")), _n(c.get("gammas_sell"))
        pgb, pgs = _n(p.get("gammas_buy")), _n(p.get("gammas_sell"))
        xrows.append({
            "k": k,
            "cIVv": c.get("volatility"), "pIVv": p.get("volatility"),
            "cGbuy": cgb, "cGsell": cgs, "pGbuy": pgb, "pGsell": pgs,
            # customers buy -> dealers short: dealer gamma from measured flow
            "rGex": -((cgb - cgs) + (pgb - pgs)) / B,
            "cGxoi": _n(c.get("gxoi")) / B, "pGxoi": _n(p.get("gxoi")) / B,
            "cGxvolm": _n(c.get("gxvolm")) / B, "pGxvolm": _n(p.get("gxvolm")) / B,
            "cVannaxoi": _n(c.get("vannaxoi")) / B, "pVannaxoi": _n(p.get("vannaxoi")) / B,
            "cCharmxoi": _n(c.get("charmxoi")) / B, "pCharmxoi": _n(p.get("charmxoi")) / B,
            "cOIch": c.get("oi_ch"), "pOIch": p.get("oi_ch"),
            "cV5m": c.get("volm_5m"), "pV5m": p.get("volm_5m"),
            "cVal5m": c.get("value_5m"), "pVal5m": p.get("value_5m"),
            "cDbuy": _n(c.get("deltas_buy")), "cDsell": _n(c.get("deltas_sell")),
            "pDbuy": _n(p.get("deltas_buy")), "pDsell": _n(p.get("deltas_sell")),
            # ask-bid computed here: the vendor `spread` field returns -bid, which is garbage
            "cSpread": (_n(c.get("ask_price")) - _n(c.get("bid_price"))),
            "pSpread": (_n(p.get("ask_price")) - _n(p.get("bid_price"))),
        })
    return rows, xrows


def exp_date(head):
    """Convex exp head is a day_id (days since epoch)."""
    try:
        return (datetime.date(1970, 1, 1) + datetime.timedelta(days=int(head))).isoformat()
    except Exception:
        return str(head)


def atm_iv(spot, step, book, T, F):
    """sigma = the CALL IV at the centre strike (ToS R3), from parity inversion."""
    centre = round(spot / step) * step
    cp = book.get(centre)
    if cp:
        v = implied(mid(cp[0]), F, centre, T, "C")
        if v:
            return v, centre
    # fall back outward to the nearest strike that inverts
    for off in range(1, HALF_WIN + 1):
        for k in (centre + off * step, centre - off * step):
            cp = book.get(k)
            if not cp:
                continue
            v = implied(mid(cp[0]), F, k, T, "C")
            if v:
                return v, centre
    return 0.0, centre


WEEK_N = 40   # matches Mithil's LiveLog_v18 edit: +-40 steps (81 strikes)
END_HH = 17   # stop with the collector, before the ~18:00 buy/sell counter reset
SYMS = ("SPX", "SPY", "QQQ")


def build_symbol(sym, dec, today, state):
    d = dec.get(sym)
    if not d or not d["exps"]:
        return None, None
    spot = d["spot"]
    step = STEP.get(sym, 1.0)
    head0, book0 = d["exps"][0]
    exp0 = exp_date(head0)
    dte = max((datetime.date.fromisoformat(exp0) - today).days, 0)
    T = max(dte, MIN_T_DAYS) / 365.0          # Chain!R1 - greek columns only
    Tiv = t_iv() if dte == 0 else (dte / 365.0)  # RTD IMPL_VOL - IV inversion

    pairs = [(k, c, p) for k, (c, p) in book0.items()]
    F, Fsd = parity_forward(pairs, spot)
    sigma, centre = atm_iv(spot, step, book0, Tiv, F)

    # cIV / pIV: invert each side where it is stable; unstable -> 0, which is
    # exactly what ToS shows for deep-ITM contracts (observed cIV=0 at 7620).
    iv_map = {}
    for k in window_strikes(spot, step):
        cp = book0.get(k)
        if not cp:
            continue
        iv_map[(k, "C")] = implied(mid(cp[0]), F, k, Tiv, "C")
        iv_map[(k, "P")] = implied(mid(cp[1]), F, k, Tiv, "P")

    rows, xrows = tos_rows(sym, spot, book0, T, sigma, iv_map)
    if not rows:
        return None, None

    cc = book0.get(centre)
    straddle = 0.0
    if cc:
        mc, mp = mid(cc[0]), mid(cc[1])
        straddle = (mc or 0.0) + (mp or 0.0)

    # term[]: one entry per pulled expiry
    term = []
    for head, bk in d["exps"]:
        ed = exp_date(head)
        try:
            dt_i = max((datetime.date.fromisoformat(ed) - today).days, 0)
        except Exception:
            continue
        Ti = t_iv() if dt_i == 0 else (dt_i / 365.0)
        Fi, _ = parity_forward([(k, c, p) for k, (c, p) in bk.items()], spot)
        ci = round(spot / step) * step
        cp = bk.get(ci)
        if not cp:
            continue
        mc, mp = mid(cp[0]) or 0.0, mid(cp[1]) or 0.0
        sv, _ = atm_iv(spot, step, bk, Ti, Fi)
        term.append({"dte": dt_i, "iv": sv, "straddle": mc + mp,
                     "move": ((mc + mp) / spot) if spot else 0.0})

    # rolling 15s/60s series, persisted so a restart does not lose them
    hhmm = now_et().strftime("%H:%M")
    gexT = sum(r["gex"] for r in rows)
    dexT = sum(r["dex"] for r in rows)
    vanT = sum(r["vanna"] for r in rows)
    chaT = sum(r["charm"] for r in rows)
    cvT = sum(r["cV"] for r in rows)
    pvT = sum(r["pV"] for r in rows)
    focus = max(rows, key=lambda r: abs(r["gex"]))
    tl = state.setdefault("tl", {}).setdefault(sym, [])
    ser = state.setdefault("ser", {}).setdefault(sym, [])
    tl.append([hhmm, spot, focus["cM"], focus["pM"]])
    # ToS ser order is cols V,W,Y,X = gex, dex, VANNA, CHARM (vanna before charm)
    ser.append([hhmm, gexT, dexT, vanT, chaT, cvT, pvT, straddle])
    del tl[:-121]
    del ser[:-121]

    opens = state.setdefault("open", {})
    if sym not in opens:
        opens[sym] = spot          # replaced by the CVForge backfill when it runs
    op = opens[sym]

    out = {
        "spot": spot, "open": op,
        "pct": (spot / op - 1.0) if op else 0.0,
        "iv": sigma, "iv_open": 0,
        "straddle": straddle,
        "exp": exp0, "step": int(step) if step == int(step) else step,
        "rows": rows, "tl": list(tl), "ser": list(ser), "term": term,
        "hv": {"hv20": 0, "sqlog20": 0, "hv252": 0},
        # --- additive, ignored by the v1 map ---
        "xrows": xrows,
        "forward": F, "forward_sd": Fsd, "basis": F - spot,
        "focus_k": focus["k"], "dte": dte,
        "T_greeks": T, "T_iv": Tiv, "mins_to_close": minutes_to_close(),
        "n_strikes_available": len(book0),
    }

    flow = {"t": hhmm, "d": today.isoformat(), "s": sym, "x": spot,
            "k": [r["k"] for r in rows],
            "cv": [r["cV"] for r in rows], "pv": [r["pV"] for r in rows],
            "cm": [r["cM"] for r in rows], "pm": [r["pM"] for r in rows],
            "ci": [r["cIV"] for r in rows], "pi": [r["pIV"] for r in rows],
            "cb": [r["cB"] for r in rows], "ca": [r["cA"] for r in rows],
            "cl": [r["cL"] for r in rows], "cz": [r["cZ"] for r in rows],
            "cd": [r["cD"] for r in rows],
            "pb": [r["pB"] for r in rows], "pa": [r["pA"] for r in rows],
            "pl": [r["pL"] for r in rows], "pz": [r["pZ"] for r in rows],
            "pd": [r["pD"] for r in rows]}
    return out, flow


def build_week(dec, today):
    d = dec.get("SPX")
    if not d or len(d["exps"]) < 2:
        return {}
    spot = d["spot"]
    step = STEP["SPX"]
    centre = round(spot / step) * step
    ks = [centre + step * i for i in range(-WEEK_N, WEEK_N + 1)]
    rows = []
    for head, bk in d["exps"][1:]:
        ed = exp_date(head)
        try:
            dte = max((datetime.date.fromisoformat(ed) - today).days, 0)
        except Exception:
            continue
        Fi, _ = parity_forward([(k, c, p) for k, (c, p) in bk.items()], spot)
        Ti = t_iv() if dte == 0 else (dte / 365.0)
        for k in ks:
            cp = bk.get(k)
            if not cp:
                continue
            c, p = cp
            rows.append({"e": ed, "dte": dte, "k": k,
                         "cOI": _n(c.get("oi")), "cIV": implied(mid(c), Fi, k, Ti, "C") or 0,
                         "cD": _n(c.get("delta")),
                         "pOI": _n(p.get("oi")), "pIV": implied(mid(p), Fi, k, Ti, "P") or 0,
                         "pD": _n(p.get("delta"))})
    # NOTE: re-centred on EVERY build. ToS stamps `built` once and never
    # re-centres, which is why its week book reports the wrong gamma sign above
    # its top strike. This is the one intentional behavioural fix.
    return {"SPX": {"built": "%s|%d" % (today.isoformat(), centre), "rows": rows}}


# VIX9D is not carried by Convex (probed 2026-10-27: not in `und`). Cboe serves it
# free on the CDN the trading-intel CboeClient already uses. It is NOT Convex, so it
# costs no API budget. If it fails the cockpit degrades on its own to
# 1/2 * delta-0DTE-ATM-IV.
# FIX 2026-10-28: the /indices/ path returns 403 for every symbol; /quotes/ works
# (VIX9D 16.47 at 13:42). Until this fix v9d was always 0 on the Convex cockpit.
_CBOE = "https://cdn-api.cboe.com/api/global/delayed_quotes/quotes/"
_v9d = {"val": 0.0, "at": 0.0}
V9D_INTO_MAP = False   # flip only on Mithil's OK (MAP rule) - see process()



def _rec_now(rec):
    """The pull's own ET timestamp, so a --rebuild replays expiry timing correctly."""
    try:
        return datetime.datetime.fromisoformat(str(rec.get("ts"))[:19])
    except Exception:
        return now_et()


def vix_rows(dv, now=None):
    import datetime as _dt
    """VIX chain -> compact rows for the VIX dashboard.

    Additive: nothing else in live.json changes. Defensive by design - this runs
    inside the live writer, so any failure here must not take the cockpit down.
    """
    out = []
    try:
        v = dv.get("VIX") or {}
        S = _n(v.get("spot"))
        for head, book in (v.get("exps") or []):
            rows = []
            for k in sorted(book):
                c, p = book[k]
                rows.append({
                    "k": k,
                    "cOI": _n(c.get("oi")),   "pOI": _n(p.get("oi")),
                    "cV":  _n(c.get("volm")), "pV":  _n(p.get("volm")),
                    "cIV": _n(c.get("volatility")), "pIV": _n(p.get("volatility")),
                    "cD":  _n(c.get("delta")),     "pD":  _n(p.get("delta")),
                    "cG":  _n(c.get("gamma")),     "pG":  _n(p.get("gamma")),
                    "cVan": _n(c.get("vanna")),    "pVan": _n(p.get("vanna")),
                    "cGb": _n(c.get("gammas_buy")),  "cGs": _n(c.get("gammas_sell")),
                    "pGb": _n(p.get("gammas_buy")),  "pGs": _n(p.get("gammas_sell")),
                    "cPx": _n(c.get("price")),     "pPx": _n(p.get("price")),
                })
            if rows:
                try:   # head is days-since-epoch on the VIX chain
                    lbl = (_dt.date(1970, 1, 1) + _dt.timedelta(days=int(head))).isoformat()
                except Exception:
                    lbl = str(head)[:10]
                item = {"e": lbl, "n": len(rows), "rows": rows}
                if vixdash is not None:
                    try:
                        extra = vixdash.enrich_expiry(lbl, book, now or now_et())
                    except Exception as exc:
                        log("vix enrich %s: %s" % (lbl, str(exc)[:80]))
                        extra = {}
                    if extra is None:      # settled this morning, or listed but empty
                        continue
                    item.update(extra)
                out.append(item)
    except Exception as exc:
        log("vix_rows: %s" % str(exc)[:80])
    return out


def vix9d():
    import time as _t
    if vixdash is not None:            # the regime thread already polls VIX9D
        q = vixdash.quote("VIX9D")
        if q > 0:
            return q
    if _t.time() - _v9d["at"] < 60:
        return _v9d["val"]
    _v9d["at"] = _t.time()
    try:
        import requests
        r = requests.get(_CBOE + "_VIX9D.json", timeout=10,
                         headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200:
            d = r.json().get("data") or {}
            v = d.get("last_trade_price") or d.get("current_price") or 0
            _v9d["val"] = float(v or 0)
        else:
            log("vix9d: Cboe HTTP %s" % r.status_code)
    except Exception as exc:
        log("vix9d: %s" % str(exc)[:80])
    return _v9d["val"]


SHARED = ("ti.json", "blocks.json")


def mirror_shared():
    """Copy the feeds both cockpits share (Doc's levels, the tape) into data/.

    ti.json comes from am_feed.py and blocks.json from blocks_feed.py; both are
    feed-agnostic, so the Convex cockpit reads the SAME ones rather than a
    duplicate pipeline. Read-only from our side - we never write the originals.
    """
    import shutil
    # Laptop: _cvxmap sits inside C:\tos-dashboard, so the parent holds them.
    # NAS: scripts/cvxmap has no such parent, so look in data/ itself (something
    # else may drop them there) and in an optional sibling 'shared' folder.
    roots = [os.path.dirname(HERE), os.path.join(os.path.dirname(HERE), "shared"), DATA]
    for name in SHARED:
        src = next((os.path.join(r, name) for r in roots
                    if os.path.exists(os.path.join(r, name))), None)
        if src is None or os.path.abspath(src) == os.path.abspath(os.path.join(DATA, name)):
            continue
        try:
            shutil.copyfile(src, os.path.join(DATA, name))
        except Exception as exc:
            log("mirror %s failed: %s" % (name, exc))


def atomic_write(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, separators=(",", ":"))
    os.replace(tmp, path)


def process(rebuild=False):
    day = now_et().strftime("%Y%m%d")
    raw = os.path.join(DATA, "raw_%s.ndjson.gz" % day)
    if not os.path.exists(raw):
        log("no raw file yet: %s" % raw)
        return 0
    spath = os.path.join(DATA, "live_state_%s.json" % day)
    state = {}
    if os.path.exists(spath) and not rebuild:
        try:
            state = json.load(open(spath, encoding="utf-8"))
        except Exception:
            state = {}
    done = 0 if rebuild else int(state.get("lines_done", 0))

    recs = []
    with gzip.open(raw, "rt", encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            if i < done:
                continue
            try:
                recs.append((i, json.loads(line)))
            except Exception:
                continue
    if not recs:
        return 0

    today = today_et()
    flow_path = os.path.join(DATA, "flow_%s.ndjson" % day)
    if rebuild and os.path.exists(flow_path):
        # A rebuild replays every raw line, so the live lines must be cleared
        # first or the day is duplicated. Backfilled lines (_bf) are NOT ours
        # to touch - they come from CVForge and cannot be regenerated here.
        kept = []
        for line in open(flow_path, encoding="utf-8"):
            try:
                j = json.loads(line)
            except Exception:
                continue
            if j.get("_bf"):
                kept.append(line.rstrip("\n"))
        tmp = flow_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            if kept:
                fh.write("\n".join(kept) + "\n")
        os.replace(tmp, flow_path)
        log("rebuild: cleared live flow lines, kept %d backfilled" % len(kept))
    n = 0
    last_live = None
    pending_vix = state.get("_vix") or {}
    for i, rec in recs:
        if rec.get("pull") == "vix":
            try:
                dv = decode(rec)
                pending_vix = {"spot": dv["VIX"]["spot"], "v9d": 0,
                               "exps": vix_rows(dv, _rec_now(rec))}
            except Exception:
                pass
            state["_vix"] = pending_vix
            state["lines_done"] = i + 1
            continue
        if rec.get("pull") != "idx":
            state["lines_done"] = i + 1
            continue
        try:
            dec = decode(rec)
        except Exception:
            log("decode failed on line %d" % i)
            state["lines_done"] = i + 1
            continue
        live = {"ts": rec.get("ts")}
        flows = []
        for sym in SYMS:
            sd, fl = build_symbol(sym, dec, today, state)
            if sd:
                live[sym] = sd
                flows.append(fl)
        vx = dict(pending_vix) if pending_vix else {"spot": 0, "v9d": 0}
        # MAP RULE: the page's week-book vol re-mark reads VIX.v9d (falls back to
        # 1/2 * delta 0DTE ATM IV when it is 0). It has been 0 on Convex since day
        # one (the 403 above), so a real VIX9D here would CHANGE the map. Held at
        # the old value until Mithil says otherwise; the VIX tab reads v9dq.
        v9 = vix9d()
        vx["v9d"] = v9 if V9D_INTO_MAP else 0
        vx["v9dq"] = v9
        live["VIX"] = vx
        if vixdash is not None:
            try:
                live["VIXR"] = vixdash.regime_snapshot()
                live["EVT"] = vixdash.events(today)
            except Exception as exc:
                log("vix regime/events: %s" % str(exc)[:80])
        live["ES"] = {"last": 0, "cvd": 0, "tl": []}
        live["week"] = build_week(dec, today)
        live["_source"] = "convex"
        if flows and flows[0]["t"] <= "16:15":
            with open(flow_path, "a", encoding="utf-8") as fh:
                for fl in flows:
                    fh.write(json.dumps(fl, separators=(",", ":")) + "\n")
        last_live = live
        state["lines_done"] = i + 1
        n += 1

    if last_live:
        atomic_write(os.path.join(DATA, "live.json"), last_live)
    mirror_shared()
    atomic_write(spath, state)
    return n


def main():
    os.makedirs(DATA, exist_ok=True)
    once = "--once" in sys.argv
    rebuild = "--rebuild" in sys.argv
    log("stage2 start  once=%s rebuild=%s" % (once, rebuild))
    if vixdash is not None:
        try:
            vixdash.start_regime(now_et)
            log("vixdash loaded - regime thread started (Cboe, no Convex calls)")
        except Exception as exc:
            log("vix regime thread: %s" % str(exc)[:80])
    else:
        log("vixdash NOT loaded - VIX tab extras off, writer otherwise unchanged")
    first = True
    while True:
        n_et = now_et()
        if not (once or rebuild) and (n_et.weekday() >= 5 or n_et.hour >= END_HH):
            log("window closed - exiting")
            break
        try:
            n = process(rebuild=rebuild and first)
            log("pass: %d new idx pull(s)%s" % (n, " -> live.json + flow" if n else " (idle)"))
        except Exception:
            log("ERROR\n" + traceback.format_exc())
        first = False
        if once or rebuild:
            break
        time.sleep(20)


if __name__ == "__main__":
    main()
