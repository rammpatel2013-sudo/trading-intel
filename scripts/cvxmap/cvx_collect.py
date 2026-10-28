"""
Convex cockpit collector - STAGE 1: raw per-minute chain dump.
Created 2026-10-27.

STANDALONE. Reads only the trading-intel .env for credentials. Writes only into
_cvxmap\data\. Touches nothing in C:\tos-dashboard (no live.json, no workbook,
no blocks.json). Safe to run alongside LiveLog and blocks_feed.py.

Why stage 1 exists on its own: ConvexValue serves NO intraday history, so any
session we do not log is permanently unrecoverable. This banks every field of
every pull, so the cockpit-schema transform (stage 2) can be built, fixed and
re-run against today's data afterwards.

Run:  C:\tos-dashboard\_cvxmap\run_collector_20261027.bat
"""
import os, sys, json, math, time, datetime, traceback, gzip
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
BASE = "https://convexvalue.com"

ENV_CANDIDATES = [
    "/app/.env",                                   # NAS: run_cvxmap.sh bind-mounts it here
    r"C:\Users\drmit\PycharmProjects\trading-intel\.env",   # laptop
    os.path.join(HERE, ".env"),
    os.path.expanduser("~/mnt/trading-intel/.env"),
]

# Every field we can get. Superset of what the cockpit needs, because
# re-collecting is impossible and an unused column costs nothing.
PARAMS = [
    # --- raw market data (cockpit rows[] needs these verbatim) ---
    "bid_price","ask_price","bid_size","ask_size","price","size","spread",
    "volm","oi","oi_ch","day_volume","theo","volatility",
    # --- greeks (we recompute from parity-forward IV; these cross-check) ---
    "delta","gamma","vanna","charm","vomma","theta","vega",
    # --- vendor exposures, OI basis and VOLUME basis ---
    "gxoi","dxoi","vxoi","txoi","vannaxoi","vommaxoi","charmxoi",
    "gxvolm","dxvolm","vxvolm","txvolm","vannaxvolm","vommaxvolm","charmxvolm",
    # --- REAL signing: measured buy/sell split (the extra fields v1 ignores) ---
    "gammas_buy","gammas_sell","deltas_buy","deltas_sell",
    "volm_buy","volm_sell","value_buy","value_sell",
    # --- rolling windows + session context ---
    "volm_5m","value_5m",
    "day_open_price","day_high_price","day_low_price","prev_day_close_price",
]

# Verified 2026-10-27: these pass schema validation but the CHAIN endpoint returns
# NOTHING for them per contract - they are underlying-level fields on another
# endpoint. Requesting them silently shortens every row and misaligns zip().
# Kept here as documentation so nobody re-adds them to PARAMS.
NOT_CHAIN_PARAMS = ["flownet","vflownet","flowratio","vflowratio",
                    "prop1","prop2","prop3","prop4",
                    "forward_price","interest","dividend"]

# Pull plan. One request per entry.
PULLS = [
    {"name":"idx", "symbols":["SPX","SPY","QQQ"], "exps":list(range(12)), "rng":0.035},
    {"name":"vix", "symbols":["VIX"],             "exps":list(range(6)), "rng":1.30},
]

POLL_S     = 60
END_HH     = 17          # stop at 17:00 ET, before the ~18:00 buy/sell counter reset
MAX_PER_MIN = 3          # our own share; blocks_feed takes ~1/min, NAS tas ~2/min of 7
BUCKET     = os.path.join(DATA, "convex_tokens.json")

# Always reason in Eastern, never in host-local time. The laptop runs ET so naive
# local would work there, but NAS containers run UTC and this moves to the NAS.
try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:
    _ET = None


def now_et():
    if _ET is not None:
        return datetime.datetime.now(_ET).replace(tzinfo=None)
    return datetime.datetime.utcnow() - datetime.timedelta(hours=4)


def log(msg):
    stamp = now_et().strftime("%Y-%m-%d %H:%M:%S")
    line = "%s  %s" % (stamp, msg)
    print(line, flush=True)
    try:
        with open(os.path.join(DATA, "collector_%s.log" % now_et().strftime("%Y%m%d")),
                  "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def creds():
    for p in ENV_CANDIDATES:
        if not os.path.exists(p):
            continue
        e = {}
        with open(p, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                e[k.strip()] = v.split("#")[0].strip().strip('"').strip("'")
        if e.get("CONVEX_EMAIL") and e.get("CONVEX_PASSWORD"):
            log("creds loaded from %s" % p)
            return e
    raise SystemExit("no .env with CONVEX_EMAIL/CONVEX_PASSWORD found")


def take_token(n=1, timeout=45.0):
    """File-based token bucket so several LAPTOP processes share the budget.
    NOTE: the NAS tas daemon cannot see this file - its 2/min is budgeted for
    by MAX_PER_MIN above, not enforced here."""
    deadline = time.time() + timeout
    while True:
        st = {"w": 0.0, "n": 0}
        try:
            with open(BUCKET, encoding="utf-8") as fh:
                st = json.load(fh)
        except Exception:
            pass
        now = time.time()
        if now - st.get("w", 0) >= 60.0:
            st = {"w": now, "n": 0}
        if st["n"] + n <= MAX_PER_MIN:
            st["n"] += n
            try:
                with open(BUCKET, "w", encoding="utf-8") as fh:
                    json.dump(st, fh)
            except Exception:
                pass
            return True
        if time.time() > deadline:
            log("TOKEN timeout - proceeding anyway (bucket may be stale)")
            return False
        time.sleep(2.0)


def login():
    e = creds()
    S = requests.Session()
    r = S.post(BASE + "/api/access/login",
               json={"email": e["CONVEX_EMAIL"], "password": e["CONVEX_PASSWORD"]}, timeout=30)
    r.raise_for_status()
    log("login ok")
    return S


def pull(S, spec):
    take_token(1)
    body = {"symbols": spec["symbols"], "params": PARAMS,
            "exps": spec["exps"], "rng": spec["rng"]}
    t0 = time.time()
    r = S.post(BASE + "/api/core/get/chain", json=body, timeout=90)
    el = time.time() - t0
    if r.status_code == 401:
        raise PermissionError("401")
    r.raise_for_status()
    j = r.json()
    # Guard: the endpoint silently drops params it will not serve per contract,
    # which shifts every subsequent column. Fail loudly instead of banking garbage.
    try:
        d = j.get("data") or []
        if d and d[0].get("chain"):
            width = len(d[0]["chain"][0][1][0][1]) - 1
            if width != len(PARAMS):
                log("PARAM WIDTH MISMATCH: requested %d, server returned %d - "
                    "columns are misaligned, FIX PARAMS before trusting this data"
                    % (len(PARAMS), width))
    except Exception:
        pass
    return j, el, len(r.content)


def main():
    os.makedirs(DATA, exist_ok=True)
    log("=" * 70)
    log("collector start  poll=%ds  end=%02d:00  params=%d  pulls=%d"
        % (POLL_S, END_HH, len(PARAMS), len(PULLS)))
    S = login()
    cycles = 0
    while True:
        now = now_et()
        if now.weekday() >= 5 or now.hour >= END_HH:
            log("window closed - exiting after %d cycles" % cycles)
            break
        raw_path = os.path.join(DATA, "raw_%s.ndjson.gz" % now.strftime("%Y%m%d"))
        for spec in PULLS:
            try:
                j, el, nbytes = pull(S, spec)
            except PermissionError:
                log("session expired - re-login")
                try:
                    S = login()
                    j, el, nbytes = pull(S, spec)
                except Exception as exc:
                    log("re-login FAILED %s: %s" % (spec["name"], exc))
                    continue
            except Exception as exc:
                log("pull FAILED %s: %s" % (spec["name"], exc))
                continue
            rec = {"ts": now.isoformat(timespec="seconds"), "pull": spec["name"],
                   "symbols": spec["symbols"], "exps": spec["exps"], "rng": spec["rng"],
                   "params": PARAMS, "elapsed_s": round(el, 3), "bytes": nbytes,
                   "data": j.get("data")}
            try:
                with gzip.open(raw_path, "at", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
            except Exception as exc:
                log("WRITE FAILED %s: %s" % (spec["name"], exc))
                continue
            log("%-4s %d sym  %5.2fs  %7.1f KB" % (spec["name"], len(spec["symbols"]), el, nbytes / 1024.0))
        cycles += 1
        time.sleep(max(1, POLL_S - (now_et() - now).total_seconds()))
    log("done - %d cycles" % cycles)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("interrupted by user")
    except Exception:
        log("FATAL\n" + traceback.format_exc())
        sys.exit(1)
