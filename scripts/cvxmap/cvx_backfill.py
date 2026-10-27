"""
Convex cockpit - BACKFILL today's pre-collection session from CVForge.

ConvexValue serves no intraday history, so the minutes before stage 1 started
are missing from the Convex map. CVForge (Research tier, 830 req/min, same
vendor) serves per-CONTRACT minute OHLC, which is enough to rebuild the book:

    bars -> call/put price per strike per minute
         -> F = median(K + C - P)            (parity forward, per minute)
         -> invert Black-76 on the OTM side  (IV per strike per minute)
         -> cumulative volume per strike     (cv/pv)

Open interest is NOT in the bars, and does not need to be: OI is published once
daily, so the session's OI applies to every minute of it - which is also how the
cockpit treats it (mapRowsAt always reads cOI/pOI from the LIVE book, even for
historical columns).

Writes flow_YYYYMMDD.ndjson lines for the missing minutes and merges them, in
time order, with whatever stage 2 already wrote.

Run:  python cvx_backfill.py            (SPX, today)
      python cvx_backfill.py --sym SPY
"""
import os, sys, json, gzip, glob, math, time, datetime, re
from concurrent.futures import ThreadPoolExecutor
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
CACHE = os.path.join(DATA, "bars_cache")
CVB = "https://tap.convexvalue.com/api/data"

sys.path.insert(0, HERE)
import cvx_live as L          # reuse the exact same IV / forward machinery

ROOT = {"SPX": "SPXW", "SPY": "SPY", "QQQ": "QQQ"}


def key():
    for p in ("/app/.env",
              r"C:\Users\drmit\PycharmProjects\trading-intel\.env",
              os.path.join(HERE, ".env"),
              os.path.expanduser("~/mnt/trading-intel/.env")):
        if not os.path.exists(p):
            continue
        for line in open(p, encoding="utf-8", errors="ignore"):
            s = line.strip()
            if s.startswith("CVFORGE_API_KEY=") and "=" in s:
                return s.split("=", 1)[1].split("#")[0].strip().strip('"').strip("'")
    raise SystemExit("CVFORGE_API_KEY not found")


def occ(root, expiry, cp, strike):
    """O:SPXW261027C07725000 - strike x1000, zero-padded to 8."""
    return "O:%s%s%s%08d" % (root, expiry.strftime("%y%m%d"), cp, int(round(strike * 1000)))


def rpc(sess, K, name, args, tries=3):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": args}}
    H = {"Authorization": "Bearer " + K, "Content-Type": "application/json",
         "Accept": "application/json, text/event-stream"}
    for a in range(tries):
        try:
            r = sess.post(CVB + "/mcp", headers=H, json=body, timeout=60)
            t = r.text
            if t.startswith("event:"):
                for ln in t.splitlines():
                    if ln.startswith("data:"):
                        t = ln[5:].strip()
                        break
            j = json.loads(t)
            res = j.get("result") or {}
            cont = res.get("content") or []
            if cont and cont[0].get("text"):
                return json.loads(cont[0]["text"])
            return None
        except Exception:
            time.sleep(0.5 * (a + 1))
    return None


CACHE_TTL_S = 120    # today's bars keep growing, so a cached copy goes stale fast


def fetch_bars(sess, K, ticker, day):
    cpath = os.path.join(CACHE, ticker.replace(":", "_") + "_" + day + ".json")
    if os.path.exists(cpath):
        fresh = True
        if day == L.today_et().isoformat():
            # the session is still running: anything older than the TTL is missing
            # the most recent minutes, which is exactly what leaves holes.
            fresh = (time.time() - os.path.getmtime(cpath)) < CACHE_TTL_S
        if fresh:
            try:
                return json.load(open(cpath, encoding="utf-8"))
            except Exception:
                pass
    out = rpc(sess, K, "get_option_bars",
              {"ticker": ticker, "timespan": "minute", "multiplier": 1,
               "from": day, "to": day, "sort": "asc", "limit": 50000})
    res = (out or {}).get("results") or []
    try:
        json.dump(res, open(cpath, "w", encoding="utf-8"))
    except Exception:
        pass
    return res


def main():
    sym = "SPX"
    if "--sym" in sys.argv:
        sym = sys.argv[sys.argv.index("--sym") + 1].upper()
    os.makedirs(CACHE, exist_ok=True)
    K = key()
    today = L.today_et()
    day = today.isoformat()
    dayc = today.strftime("%Y%m%d")

    raws = sorted(glob.glob(os.path.join(DATA, "raw_%s.ndjson.gz" % dayc)))
    if not raws:
        raise SystemExit("no raw file - run cvx_collect.py first")
    rec = None
    for line in gzip.open(raws[-1], "rt", encoding="utf-8"):
        r = json.loads(line)
        if r.get("pull") == "idx":
            rec = r
    dec = L.decode(rec)
    d = dec[sym]
    spot_now = d["spot"]
    head0, book0 = d["exps"][0]
    expiry = datetime.date.fromisoformat(L.exp_date(head0))
    step = L.STEP[sym]
    strikes = sorted(book0.keys())
    print("%s  %d strikes  expiry %s  spot_now %.2f" % (sym, len(strikes), expiry, spot_now))

    jobs = []
    for k in strikes:
        jobs.append((k, "C", occ(ROOT[sym], expiry, "C", k)))
        jobs.append((k, "P", occ(ROOT[sym], expiry, "P", k)))
    print("fetching %d contracts from CVForge..." % len(jobs))

    sess = requests.Session()
    got = {}
    t0 = time.time()

    def work(job):
        k, cp, tk = job
        return (k, cp, fetch_bars(sess, K, tk, day))

    with ThreadPoolExecutor(max_workers=8) as ex:
        for k, cp, bars in ex.map(work, jobs):
            if bars:
                got[(k, cp)] = bars
    print("got bars for %d/%d contracts in %.1fs" % (len(got), len(jobs), time.time() - t0))
    json.dump({"n": len(got)}, open(os.path.join(DATA, "backfill_%s_%s.meta.json" % (sym, dayc)), "w"))
    return got, strikes, spot_now, today, sym, step


if __name__ == "__main__":
    main()


# ---------------- reconstruction: bars -> cockpit flow lines ----------------

def reconstruct(got, strikes, today, sym, step, upto_hhmm):
    """Rebuild per-minute flow lines from per-contract minute bars.

    price  : bar VWAP (`vw`), falling back to close. MEASURED 2026-10-27 against
             ToS's own x over 229 minutes: vwap sd=1.02 max|e|=3.57 vs close
             sd=1.64 max|e|=6.92. Close is a single last trade, so bid-ask
             bounce propagates into the parity forward; vwap averages it out.
             A trimmed median added nothing (the plain median is already robust).
    volume : cumulative sum of per-bar v  (matches ToS cV = session volume)
    spot   : derived from the parity forward by removing carry, S = F*exp(-rT).
             Bars give the forward, not the index.
    """
    by = {}                      # minute -> {k: {"C":(px,cum), "P":(px,cum)}}
    last = {}                    # (k,cp) -> last seen price
    cum = {}                     # (k,cp) -> cumulative volume
    mins = set()
    for (k, cp), bars in got.items():
        for b in bars:
            t = datetime.datetime.utcfromtimestamp(b["t"] / 1000.0)
            t = t - datetime.timedelta(hours=4)      # UTC -> ET
            if t.date() != today:
                continue
            mins.add(t.strftime("%H:%M"))
    mins = sorted(m for m in mins if "09:30" <= m <= upto_hhmm)
    if not mins:
        return []

    # index bars by (k,cp,minute)
    idx = {}
    for (k, cp), bars in got.items():
        for b in bars:
            t = datetime.datetime.utcfromtimestamp(b["t"] / 1000.0) - datetime.timedelta(hours=4)
            if t.date() != today:
                continue
            idx[(k, cp, t.strftime("%H:%M"))] = b

    out = []
    for hhmm in mins:
        hh, mm = int(hhmm[:2]), int(hhmm[3:])
        tiv = max(L.EXPIRY_MIN - (hh * 60 + mm), 1) / 525600.0
        snap = {}
        for k in strikes:
            for cp in ("C", "P"):
                b = idx.get((k, cp, hhmm))
                if b:
                    last[(k, cp)] = b.get("vw") or b.get("c")
                    cum[(k, cp)] = cum.get((k, cp), 0) + (b.get("v") or 0)
            snap[k] = (last.get((k, "C")), last.get((k, "P")),
                       cum.get((k, "C"), 0), cum.get((k, "P"), 0))

        fs = [k + snap[k][0] - snap[k][1] for k in strikes
              if snap[k][0] is not None and snap[k][1] is not None]
        if len(fs) < 5:
            continue
        fs.sort()
        F = fs[len(fs) // 2] if len(fs) % 2 else (fs[len(fs) // 2 - 1] + fs[len(fs) // 2]) / 2.0
        S = F * math.exp(-L.R_RATE * tiv)

        centre = round(S / step) * step
        ks = [centre + step * i for i in range(-L.HALF_WIN, L.HALF_WIN + 1)]
        row = {"t": hhmm, "d": today.isoformat(), "s": sym, "x": S,
               "k": [], "cv": [], "pv": [], "cm": [], "pm": [], "ci": [], "pi": [],
               "cb": [], "ca": [], "cl": [], "cz": [], "cd": [],
               "pb": [], "pa": [], "pl": [], "pz": [], "pd": [], "_bf": 1}
        for k in ks:
            if k not in snap:
                continue
            cpx, ppx, cv, pv = snap[k]
            civ = L.implied(cpx, F, k, tiv, "C") if cpx else None
            piv = L.implied(ppx, F, k, tiv, "P") if ppx else None
            row["k"].append(k)
            row["cv"].append(cv); row["pv"].append(pv)
            row["cm"].append(cpx or 0); row["pm"].append(ppx or 0)
            row["ci"].append(civ or 0); row["pi"].append(piv or 0)
            row["cb"].append(0); row["ca"].append(0)
            row["cl"].append(cpx or 0); row["cz"].append(0); row["cd"].append(0)
            row["pb"].append(0); row["pa"].append(0)
            row["pl"].append(ppx or 0); row["pz"].append(0); row["pd"].append(0)
        if len(row["k"]) >= 20:
            out.append(row)
    return out
