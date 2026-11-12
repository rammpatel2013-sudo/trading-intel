"""od_nightly.py - created 2026-11-06. Nightly optionsdata.ai bank for Mag7 + indexes.

Why: optionsdata.ai only serves TODAY's chain (its history archive starts ~Oct 2026 and is
patchy), and its trade date rolls at 17:00 America/New_York. The per-contract buy/sell
flow, open interest and exposure it serves are lost unless we save them before 17:00.
This script saves the whole chain (every expiration, every record type as served) once a
day, so our own history builds forward.

Run (NAS DSM task "od nightly", Mon-Fri 16:20 ET, user root):
    bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_script.sh od_nightly
Options:
    --symbols AAPL,MSFT   (default: Mag7 + SPX,SPY,QQQ,VIX)
    --force               run outside 16:15-17:00 ET / on weekends (testing)
    --max-dte N           skip expirations further out than N days (default 400)
    --out DIR             default reports/optionsdata
    --workers N           symbols pulled at the same time (default 3)
    --day YYYY-MM-DD      folder label; weekend catch-up of the last session, e.g.
                          run_script.sh od_nightly --force --day 2026-11-06
                          (after the 17:00 roll the flow records are gone; the rest stays)

Writes reports/optionsdata/<YYYYMMDD>/ (api_calls.csv.gz + summary "api" block added 2026-11-10:
every HTTP attempt with status, ms and any rate-limit headers, so the beta's limits are measured):
    <SYM>.csv.gz            every expiration stacked, all fields as served
    <SYM>_underlying.csv.gz the underlying snapshot + term rows
    summary.json            per symbol: rows by record, expirations, seconds, errors,
                            and day totals of buy/sell premium + volume by call/put
Reads OPTIONSDATA_API_KEY from the environment or ./.env. The key is never printed.
Standard library only. Descriptive collection (rule 4); no AI (rule 7).
"""
import argparse
import csv
import gzip
import io
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

BASE = "https://api.optionsdata.ai/v1"
ET = ZoneInfo("America/New_York")
DEFAULT_SYMS = ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "VIX", "QQQ", "SPY", "SPX"]
LIMIT_HDRS = ("x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset", "ratelimit-limit",
              "ratelimit-remaining", "ratelimit-reset", "retry-after", "x-quota-limit", "x-quota-remaining")
CALLS = []                   # one row per HTTP attempt: et, path, status, ms, limit headers
CALLS_LOCK = threading.Lock()
DEADLINE = (16, 58)          # stop starting new expiry pulls after 16:58 ET (trade date rolls 17:00)


def load_key():
    k = os.environ.get("OPTIONSDATA_API_KEY")
    if k:
        return k.strip()
    for p in (".env", "/app/.env"):
        if os.path.exists(p):
            for line in open(p, encoding="utf-8-sig"):
                line = line.strip()
                if line.startswith("OPTIONSDATA_API_KEY"):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("OPTIONSDATA_API_KEY not found (env or .env)")


def now_et():
    return datetime.now(ET)


def _log_call(path, status, t0, headers, note=""):
    lim = ""
    if headers is not None:
        lim = ";".join(f"{h}={headers.get(h)}" for h in LIMIT_HDRS if headers.get(h) is not None)
    with CALLS_LOCK:
        CALLS.append([f"{now_et():%H:%M:%S}", path, status, round((time.time() - t0) * 1000), lim, note[:80]])


def get_csv(key, path, tries=6, **query):
    url = BASE + path + ("?" + urlencode(query) if query else "")
    req = Request(url, headers={"Authorization": "Bearer " + key, "Accept": "text/csv"})
    last = None
    for attempt in range(tries):
        t0 = time.time()
        try:
            with urlopen(req, timeout=60) as r:
                txt = r.read().decode("utf-8")
                _log_call(path, r.status, t0, r.headers)
                return txt
        except HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:200]
            _log_call(path, e.code, t0, e.headers, body)
            last = f"HTTP {e.code}: {body}"
            if e.code in (429, 500, 502, 503, 504) and attempt < tries - 1:
                ra = e.headers.get("Retry-After")
                time.sleep(float(ra) if ra and ra.replace(".", "").isdigit() else 2 * (attempt + 1))
                continue
            raise RuntimeError(last)
        except (URLError, TimeoutError) as e:
            _log_call(path, "net", t0, None, str(e))
            last = f"network: {e}"
            if attempt < tries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            raise RuntimeError(last)
    raise RuntimeError(last or "unknown")


def stack(parts):
    """Stack CSV texts that may have different column sets; returns (header, rows)."""
    header, rows = [], []
    for txt in parts:
        rd = list(csv.reader(io.StringIO(txt)))
        if not rd:
            continue
        h, body = rd[0], rd[1:]
        for c in h:
            if c not in header:
                header.append(c)
        rows.append((h, body))
    out = []
    pos = {c: i for i, c in enumerate(header)}
    for h, body in rows:
        idx = [pos[c] for c in h]
        for r in body:
            row = [""] * len(header)
            for j, v in enumerate(r):
                if j < len(idx):
                    row[idx[j]] = v
            out.append(row)
    return header, out


def write_gz(path, header, rows):
    with gzip.open(path, "wt", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def totals(header, rows):
    """Day totals from flow rows at level=expiration_right: premium + volume by right and side."""
    ix = {c: i for i, c in enumerate(header)}
    need = ("record", "level", "right", "side", "premium", "volume")
    if not all(c in ix for c in need):
        return {}
    t = {}
    for r in rows:
        if r[ix["record"]] != "flow" or r[ix["level"]] != "expiration_right":
            continue
        k = f'{r[ix["right"]]}_{r[ix["side"]]}'
        a = t.setdefault(k, {"premium": 0.0, "volume": 0.0})
        try:
            a["premium"] += float(r[ix["premium"]] or 0)
            a["volume"] += float(r[ix["volume"]] or 0)
        except ValueError:
            pass
    return {k: {"premium": round(v["premium"]), "volume": round(v["volume"])} for k, v in sorted(t.items())}


def pull_symbol(key, sym, outdir, max_dte, today, deadline):
    t0 = time.time()
    res = {"symbol": sym, "expirations": 0, "pulled": 0, "rows": 0, "by_record": {}, "errors": [], "stopped_at_deadline": False}
    und = get_csv(key, "/underlyings/" + quote(sym, safe=""))
    with gzip.open(os.path.join(outdir, f"{sym}_underlying.csv.gz"), "wt", encoding="utf-8", newline="") as f:
        f.write(und)
    urows = list(csv.DictReader(io.StringIO(und)))
    exps = sorted({r["expiration"] for r in urows if r.get("record") == "term" and r.get("expiration")})
    lim = today + timedelta(days=max_dte)
    exps = [e for e in exps if date.fromisoformat(e) <= lim]
    res["expirations"] = len(exps)
    parts, failed = [], []
    for e in exps:
        if deadline and now_et().time() >= deadline:
            res["stopped_at_deadline"] = True
            break
        try:
            parts.append(get_csv(key, "/chains/" + quote(sym, safe=""), expiration=e))
            res["pulled"] += 1
        except RuntimeError as err:
            failed.append((e, str(err)[:120]))
        time.sleep(0.2)
    for wait in (10, 30, 90):          # retry passes for expirations that failed (503 "edge disconnected" comes in bursts)
        if not failed:
            break
        if deadline and now_et().time() >= deadline:
            break
        time.sleep(wait)
        again = []
        for e, why in failed:
            if deadline and now_et().time() >= deadline:
                again.append((e, why))
                continue
            try:
                parts.append(get_csv(key, "/chains/" + quote(sym, safe=""), expiration=e))
                res["pulled"] += 1
            except RuntimeError as err:
                again.append((e, str(err)[:120]))
            time.sleep(0.5)
        failed = again
    res["errors"] += [f"{e}: {why}" for e, why in failed]
    header, rows = stack(parts)
    if rows:
        write_gz(os.path.join(outdir, f"{sym}.csv.gz"), header, rows)
        rec = header.index("record") if "record" in header else None
        if rec is not None:
            for r in rows:
                res["by_record"][r[rec]] = res["by_record"].get(r[rec], 0) + 1
    res["rows"] = len(rows)
    res["day_totals"] = totals(header, rows)
    res["seconds"] = round(time.time() - t0, 1)
    return res


def api_stats(calls):
    """Limits/uptime read of the night's calls: status counts, latency, peak calls per minute."""
    by = {}
    for c in calls:
        by[str(c[2])] = by.get(str(c[2]), 0) + 1
    ok_ms = sorted(c[3] for c in calls if c[2] == 200)
    per_min = {}
    for c in calls:
        per_min[c[0][:5]] = per_min.get(c[0][:5], 0) + 1
    pct = lambda q: ok_ms[min(len(ok_ms) - 1, int(q * len(ok_ms)))] if ok_ms else None
    return {"calls": len(calls), "by_status": by, "ok_ms_p50": pct(0.5), "ok_ms_p95": pct(0.95),
            "peak_calls_per_min": max(per_min.values()) if per_min else 0,
            "throttled_429": by.get("429", 0), "edge_503": by.get("503", 0),
            "limit_headers_seen": sorted({c[4] for c in calls if c[4]})[:5]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=",".join(DEFAULT_SYMS))
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--max-dte", type=int, default=400)
    ap.add_argument("--out", default=os.path.join("reports", "optionsdata"))
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--day", help="YYYY-MM-DD label for the folder (weekend catch-up of the last session); default today ET")
    a = ap.parse_args()
    n = now_et()
    if not a.force:
        if n.weekday() >= 5:
            print(f"od_nightly.skip weekend {n:%Y-%m-%d %H:%M} ET")
            return 0
        if not ((n.hour, n.minute) >= (16, 15) and (n.hour, n.minute) < DEADLINE):
            print(f"od_nightly.skip outside 16:15-16:58 ET window ({n:%H:%M} ET); use --force to test")
            return 0
    key = load_key()
    day = datetime.strptime(a.day, "%Y-%m-%d").date() if a.day else n.date()
    outdir = os.path.join(a.out, f"{day:%Y%m%d}")
    os.makedirs(outdir, exist_ok=True)
    deadline = None if a.force else datetime.strptime(f"{DEADLINE[0]}:{DEADLINE[1]}", "%H:%M").time()
    summary = {"day": day.isoformat(), "started_et": f"{n:%H:%M:%S}", "symbols": []}
    syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    lock = threading.Lock()

    def one(sym):
        try:
            r = pull_symbol(key, sym, outdir, a.max_dte, day, deadline)
        except RuntimeError as err:
            r = {"symbol": sym, "error": str(err)[:200]}
        with lock:
            summary["symbols"].append(r)
            print("od_nightly.symbol", json.dumps({k: r.get(k) for k in ("symbol", "expirations", "pulled", "rows", "seconds", "error", "stopped_at_deadline")}), flush=True)
            with open(os.path.join(outdir, "summary.json"), "w", encoding="utf-8") as f:
                json.dump(summary, f, indent=1)

    with ThreadPoolExecutor(max_workers=max(1, a.workers)) as ex:   # biggest books first so they start early
        list(ex.map(one, sorted(syms, key=lambda x: x not in ("SPX", "SPY", "QQQ"))))
    # a symbol whose underlying call failed outright gets one more try after a pause
    redo = [r["symbol"] for r in summary["symbols"] if r.get("error")]
    if redo and (deadline is None or now_et().time() < deadline):
        time.sleep(60)
        summary["symbols"] = [r for r in summary["symbols"] if not r.get("error")]
        for sym in redo:
            one(sym)
    summary["finished_et"] = f"{now_et():%H:%M:%S}"
    summary["api"] = api_stats(CALLS)
    with gzip.open(os.path.join(outdir, "api_calls.csv.gz"), "wt", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["et", "path", "status", "ms", "limit_headers", "note"])
        w.writerows(CALLS)
    with open(os.path.join(outdir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1)
    size = sum(os.path.getsize(os.path.join(outdir, x)) for x in os.listdir(outdir))
    bad = [s["symbol"] for s in summary["symbols"] if s.get("error") or s.get("errors")]
    print(f"od_nightly.done {day} {len(summary['symbols'])} symbols {size/1e6:.1f} MB problems={bad}")
    print("od_nightly.api", json.dumps(summary["api"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
