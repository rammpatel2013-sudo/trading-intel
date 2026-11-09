"""
TEST cockpit server (created 2026-11-08) - port 8898. Production (serve_cvx.py, port 8899) is not touched.

  * GET  /  or /cvx_cockpit_test.html   -> the test page (read from disk on every request, so an update needs no restart)
  * GET  anything else                  -> the same data files production serves (scripts/cvxmap/data/), read-only
  * POST /note      -> appends one JSON line to data/test/notes_YYYYMMDD.jsonl     ("My read", toggle changes)
  * POST /readout   -> appends one JSON line to data/test/readouts_YYYYMMDD.jsonl  (15-min read-outs per toggle)
  * nightly scorer (scoring v1, score_test.py) at 17:20 ET -> data/test/scores/ (scores_YYYYMMDD.json, s5_forecasts.json,
    scoreboard.json); live.json is also archived once per 15-minute slot
  * archive thread (MAP tracker G2/H5): every intraday version of ti.json, smilebeta.json and ledger.json is copied,
    gzipped and time-stamped to data/test/archive/YYYYMMDD/, plus code.json (sha256 of the production and test code,
    and whether production still matches LOCK.json). The plain-model map is rebuilt from the raw minute files; these
    are the inputs the replay cannot rebuild.
Writes only under scripts/cvxmap/data/test/.
"""
import datetime, functools, gzip, hashlib, http.server, json, os, shutil, socketserver, sys, threading, time

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8898
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
CVX = os.path.dirname(HERE)
DATA = os.path.join(CVX, "data")
OUT = os.path.join(DATA, "test")
PAGE = os.path.join(HERE, "cvx_cockpit_test.html")
LOCK = os.path.join(HERE, "LOCK.json")
REPO = os.path.dirname(os.path.dirname(CVX))
ARCHIVE = ("ti.json", "smilebeta.json", "ledger.json")
MAXPOST = 256 * 1024
try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:
    _ET = None


def now_et():
    if _ET is not None:
        return datetime.datetime.now(_ET).replace(tzinfo=None)
    return datetime.datetime.utcnow() - datetime.timedelta(hours=5)


def sha(path):
    """sha256 after CRLF -> LF (same rule as check_lock.py / LOCK.json)"""
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read().replace(b"\r\n", b"\n")).hexdigest()
    except Exception:
        return None


def append(kind, obj):
    os.makedirs(OUT, exist_ok=True)
    n = now_et()
    obj["server_ts"] = n.strftime("%Y-%m-%dT%H:%M:%S")
    with open(os.path.join(OUT, "%s_%s.jsonl" % (kind, n.strftime("%Y%m%d"))), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, separators=(",", ":")) + "\n")


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        super().end_headers()

    def log_message(self, *a):
        pass

    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/cvx_cockpit_test.html", "/test"):
            try:
                body = open(PAGE, "rb").read()
            except Exception:
                self.send_error(500, "test page missing")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        return super().do_GET()

    def do_POST(self):
        p = self.path.split("?")[0].strip("/")
        if p not in ("note", "readout"):
            self.send_error(404)
            return
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAXPOST:
            self.send_error(413)
            return
        try:
            obj = json.loads(self.rfile.read(n).decode("utf-8"))
            if not isinstance(obj, dict):
                raise ValueError("not an object")
            append("notes" if p == "note" else "readouts", obj)
        except Exception as e:
            self.send_error(400, str(e)[:100])
            return
        self.send_response(204)
        self.end_headers()


def code_hashes():
    files = {
        "scripts/cvxmap/cvx_cockpit.html": os.path.join(CVX, "cvx_cockpit.html"),
        "scripts/cvxmap/cvx_live.py": os.path.join(CVX, "cvx_live.py"),
        "scripts/cvxmap/am_feed.py": os.path.join(CVX, "am_feed.py"),
        "scripts/cvxmap/market_cal.py": os.path.join(CVX, "market_cal.py"),
        "scripts/cvxmap/serve_cvx.py": os.path.join(CVX, "serve_cvx.py"),
        "scripts/cvxmap/cvx_collect.py": os.path.join(CVX, "cvx_collect.py"),
        "scripts/nas/run_cvxmap.sh": os.path.join(REPO, "scripts", "nas", "run_cvxmap.sh"),
    }
    out = {k: sha(v) for k, v in files.items()}
    out["served copy data/cvx_cockpit.html"] = sha(os.path.join(DATA, "cvx_cockpit.html"))
    out["test page"] = sha(PAGE)
    try:
        lock = json.load(open(LOCK))["files"]
        out["prod_lock_ok"] = all(out.get(k) == v for k, v in lock.items())
        out["prod_lock_diff"] = [k for k, v in lock.items() if out.get(k) != v]
    except Exception as e:
        out["prod_lock_ok"] = None
        out["prod_lock_err"] = str(e)[:100]
    return out


def archiver():
    seen, day_done, scored_day = {}, None, None
    while True:
        try:
            n = now_et()
            day = n.strftime("%Y%m%d")
            adir = os.path.join(OUT, "archive", day)
            if day_done != day:
                os.makedirs(adir, exist_ok=True)
                with open(os.path.join(adir, "code_%s.json" % n.strftime("%H%M%S")), "w") as fh:
                    json.dump(code_hashes(), fh, indent=1)
                day_done = day
            for name in ARCHIVE:
                src = os.path.join(DATA, name)
                try:
                    m = os.path.getmtime(src)
                except OSError:
                    continue
                if seen.get(name) == m:
                    continue
                seen[name] = m
                os.makedirs(adir, exist_ok=True)
                stamp = datetime.datetime.fromtimestamp(m, _ET).strftime("%H%M%S") if _ET else n.strftime("%H%M%S")
                dst = os.path.join(adir, "%s_%s.json.gz" % (name[:-5], stamp))
                if not os.path.exists(dst):
                    with open(src, "rb") as fi, gzip.open(dst, "wb") as fo:
                        shutil.copyfileobj(fi, fo)
            # live.json once per 15-minute slot (lets a missed read-out be rebuilt later, and the G2 proof test)
            mm = n.hour * 60 + n.minute
            if 9 * 60 + 30 <= mm <= 16 * 60 + 5 and mm % 15 <= 2:
                slot = "%02d%02d" % divmod(mm - mm % 15, 60)
                dst = os.path.join(adir, "live_%s.json.gz" % slot)
                src = os.path.join(DATA, "live.json")
                if os.path.exists(src) and not os.path.exists(dst):
                    with open(src, "rb") as fi, gzip.open(dst, "wb") as fo:
                        shutil.copyfileobj(fi, fo)
            # nightly scorer (scoring v1) at 17:20 ET, once a day
            if mm >= 17 * 60 + 20 and scored_day != day:
                scored_day = day
                try:
                    import score_test
                    days, s5, board = score_test.score_all(DATA, OUT)
                    sdir = os.path.join(OUT, "scores")
                    os.makedirs(sdir, exist_ok=True)
                    for dd, rec in days.items():
                        with open(os.path.join(sdir, "scores_%s.json" % dd.replace("-", "")), "w") as fh:
                            json.dump(rec, fh, indent=1)
                    with open(os.path.join(sdir, "s5_forecasts.json"), "w") as fh:
                        json.dump(s5, fh, indent=1)
                    with open(os.path.join(sdir, "scoreboard.json"), "w") as fh:
                        json.dump(board, fh, indent=1)
                    print("scored %d day(s)" % len(days), flush=True)
                except Exception as e:
                    print("score error:", e, flush=True)
        except Exception as e:
            print("archive error:", e, flush=True)
        time.sleep(60)


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    threading.Thread(target=archiver, daemon=True).start()
    try:   # VIX-tab toggles VX1/VX2 + Density DN6 (2026-11-08 evening): free Cboe quotes + daily history -> data/test/volx.json. Not Convex.
        sys.path.insert(0, HERE)
        import volx
        volx.start(OUT, now_et)
    except Exception as e:
        print("volx not started:", e, flush=True)
    print("TEST cockpit  http://0.0.0.0:%d/cvx_cockpit_test.html  (production stays on 8899)" % PORT, flush=True)
    Server(("", PORT), functools.partial(Handler, directory=DATA)).serve_forever()
