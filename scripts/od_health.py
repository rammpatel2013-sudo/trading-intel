"""od_health.py - created 2026-11-10. optionsdata.ai uptime + limits log during the free beta.

Why: the beta ends at the Black Friday release (2026-11-27). API limits and reliability decide
optionsdata.ai vs ConvexValue, so measure them while it is free.

Two modes:
  (default) uptime daemon - every 5 min, 09:30-16:05 ET Mon-Fri, one light call
      (/underlyings/SPX) and one chain call (SPX nearest expiry). Each row: time, call, HTTP
      status, ms, rows returned, any rate-limit headers. Exits on its own after 16:05.
      DSM task "od health", Mon-Fri 09:28 ET, user root:
          bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_script.sh od_health
  --burst - one-off limit test (run by hand, NOT scheduled): light calls back to back,
      stepping up from 1 to 8 at a time, stopping at the first 429 or after --max-calls.
      Prints the highest calls/min reached before throttling.
          sudo bash .../scripts/nas/run_script.sh od_health --burst

Writes reports/optionsdata/health/health_<YYYYMMDD>.csv (appends) and burst_<YYYYMMDD_HHMM>.csv.
End-of-day line in ~/ti_od_health.log:  od_health.day <date> uptime=..% longest_down=..min ...
Reads OPTIONSDATA_API_KEY from env or ./.env (never printed). Stdlib only. No AI (rule 7).
"""
import argparse
import csv
import io
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

BASE = "https://api.optionsdata.ai/v1"
ET = ZoneInfo("America/New_York")
OUT = os.path.join("reports", "optionsdata", "health")
LIMIT_HDRS = ("x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset", "ratelimit-limit",
              "ratelimit-remaining", "ratelimit-reset", "retry-after", "x-quota-limit", "x-quota-remaining")
COLS = ["et", "call", "status", "ms", "rows", "limit_headers", "note"]


def load_key():
    k = os.environ.get("OPTIONSDATA_API_KEY")
    if k:
        return k.strip()
    for p in (".env", "/app/.env"):
        if os.path.exists(p):
            for line in open(p, encoding="utf-8-sig"):
                if line.strip().startswith("OPTIONSDATA_API_KEY"):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("OPTIONSDATA_API_KEY not found (env or .env)")


def now_et():
    return datetime.now(ET)


def call(key, name, path, **query):
    """One request, no retries (we want to SEE failures). Returns (row, body)."""
    url = BASE + path + ("?" + urlencode(query) if query else "")
    req = Request(url, headers={"Authorization": "Bearer " + key, "Accept": "text/csv"})
    t0 = time.time()
    status, body, hdrs, note = "net", "", None, ""
    try:
        with urlopen(req, timeout=60) as r:
            status, body, hdrs = r.status, r.read().decode("utf-8", "replace"), r.headers
    except HTTPError as e:
        status, hdrs = e.code, e.headers
        note = e.read().decode("utf-8", "replace")[:80]
    except (URLError, TimeoutError, OSError) as e:
        note = str(e)[:80]
    ms = round((time.time() - t0) * 1000)
    lim = ";".join(f"{h}={hdrs.get(h)}" for h in LIMIT_HDRS if hdrs is not None and hdrs.get(h) is not None)
    rows = max(0, body.count("\n") - 1) if body else 0
    return [f"{now_et():%H:%M:%S}", name, status, ms, rows, lim, note.replace("\n", " ")], body


def append(path, rows):
    new = not os.path.exists(path)
    with open(path, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(COLS)
        w.writerows(rows)


def front_expiry(body):
    exps = sorted({r["expiration"] for r in csv.DictReader(io.StringIO(body))
                   if r.get("record") == "term" and r.get("expiration")})
    today = f"{now_et():%Y-%m-%d}"
    later = [e for e in exps if e >= today]
    return later[0] if later else None


def day_summary(path):
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    if not rows:
        return "no rows"
    slots = {}
    for r in rows:                                   # a 5-min slot is UP if every call in it returned 200
        slots.setdefault(r["et"][:5], []).append(r["status"] == "200")
    ups = [all(v) for _, v in sorted(slots.items())]
    longest = run = 0
    for u in ups:
        run = 0 if u else run + 1
        longest = max(longest, run)
    ok = sorted(int(r["ms"]) for r in rows if r["status"] == "200")
    p95 = ok[int(0.95 * (len(ok) - 1))] if ok else None
    st = {}
    for r in rows:
        st[r["status"]] = st.get(r["status"], 0) + 1
    lim = sorted({r["limit_headers"] for r in rows if r["limit_headers"]})
    return (f"uptime={100 * sum(ups) / len(ups):.1f}% slots={len(ups)} longest_down={longest * 5}min "
            f"ok_ms_p95={p95} status={st} limit_headers={lim[:2] or 'none'}")


def daemon(key, every, end_hm):
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, f"health_{now_et():%Y%m%d}.csv")
    while (now_et().hour, now_et().minute) < end_hm:
        light, body = call(key, "underlying_SPX", "/underlyings/SPX")
        batch = [light]
        exp = front_expiry(body) if light[2] == 200 else None
        if exp:
            chain, _ = call(key, "chain_SPX_front", "/chains/SPX", expiration=exp)
            batch.append(chain)
        append(path, batch)
        print("od_health.tick", " ".join(str(b[2]) + "/" + str(b[3]) + "ms" for b in batch), flush=True)
        time.sleep(max(1, every * 60 - (time.time() % (every * 60))))   # stay on the 5-min grid
    print(f"od_health.day {now_et():%Y-%m-%d} {day_summary(path)}", flush=True)


def burst(key, max_calls):
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, f"burst_{now_et():%Y%m%d_%H%M}.csv")
    done, best = 0, 0.0
    for width in (1, 2, 4, 8):
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=width) as ex:
            rows = [r for r, _ in ex.map(lambda _: call(key, f"burst_w{width}", "/underlyings/SPX"), range(width * 10))]
        append(path, rows)
        done += len(rows)
        secs = max(0.001, time.time() - t0)
        ok = sum(1 for r in rows if r[2] == 200)
        rate = 60 * ok / secs
        throttled = sum(1 for r in rows if r[2] == 429)
        print(f"od_health.burst width={width} calls={len(rows)} ok={ok} 429s={throttled} "
              f"rate={rate:.0f}/min headers={sorted({r[5] for r in rows if r[5]})[:1] or 'none'}", flush=True)
        if throttled:
            print(f"od_health.burst_result throttled at ~{rate:.0f} calls/min (width {width}); best clean ~{best:.0f}/min")
            return
        best = max(best, rate)
        if done >= max_calls:
            break
        time.sleep(5)
    print(f"od_health.burst_result no 429 up to ~{best:.0f} calls/min ({done} calls)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--burst", action="store_true", help="one-off rate-limit test (run by hand)")
    ap.add_argument("--max-calls", type=int, default=150)
    ap.add_argument("--every", type=int, default=5, help="minutes between uptime checks")
    ap.add_argument("--force", action="store_true", help="run the daemon outside market hours (testing)")
    a = ap.parse_args()
    key = load_key()
    if a.burst:
        burst(key, a.max_calls)
        return 0
    n = now_et()
    if not a.force and (n.weekday() >= 5 or not ((9, 25) <= (n.hour, n.minute) < (16, 5))):
        print(f"od_health.skip outside Mon-Fri 09:25-16:05 ET ({n:%a %H:%M}); use --force to test")
        return 0
    end = (23, 59) if a.force else (16, 5)
    daemon(key, a.every, end)
    return 0


if __name__ == "__main__":
    sys.exit(main())
