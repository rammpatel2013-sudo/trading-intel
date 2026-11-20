"""Focus names writer (2026-11-20): focus_raw_YYYYMMDD.ndjson.gz -> the cockpit's focus files + the database.

Mithil 2026-11-20: "i can select any equity and it start collecting and provide me same calculation and
visualization as index we get today. allow max 4 equity selection."

Reads ONLY focus_raw_YYYYMMDD.ndjson.gz (the collector's "focus" pull, one request for all of today's picks).
Never reads or writes raw_*.ndjson.gz, live.json, flow_*.ndjson or flow_tail.json, so the Map and every index
tab are untouched (MAP rule). Uses cvx_live.build_symbol UNCHANGED - the same rows[] / xrows[] / term / ser
maths as SPX / SPY / QQQ - with two per-process settings for single names:
  * STEP[sym] = the name's strike spacing near spot (read from the chain);
  * the 0DTE clock runs to 16:00 (stock options stop at the 16:00 close, not SPX's 16:15 settlement print).

Writes into data/:
  focus_live.json                      {"ts", "syms": [...], "<SYM>": <same shape as live.json[SPX]>}
  focus_flow_<SYM>_YYYYMMDD.ndjson     the minute log, one row per pull (same shape as flow_*.ndjson rows)
  focus_tail_<SYM>.json                the last rows of that log (same contract as flow_tail.json)
  focus_state_YYYYMMDD.json            this writer's own state
And every 5th minute (and the first pull of each name) into the database, source convex_focus:
  greeks_chain (every contract pulled) + greeks_snapshots (one row per name) - trading_intel.focus.feed.

Run (NAS, run_cvxmap.sh start):  python scripts/cvxmap/focus_live.py        (loop until 17:00 ET)
                                 python scripts/cvxmap/focus_live.py --once
"""
import collections, datetime, json, os, sys, time, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import rawio                      # noqa: E402
import cvx_live as L              # noqa: E402

DATA = os.path.join(HERE, "data")
END_HH = 17
DB_EVERY_MIN = 5
TAIL_LINES, TAIL_WINDOW = 8, 1 << 20


def log(msg):
    line = "%s  %s" % (L.now_et().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(os.path.join(DATA, "focus_%s.log" % L.now_et().strftime("%Y%m%d")), "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------- single-name settings (this process only)
L.EXPIRY_MIN = 16 * 60          # stock options: the 0DTE clock runs to the 16:00 close


def _window(spot, step):
    """cvx_live.window_strikes with the strikes rounded, so 2.5-wide strikes match the chain's keys exactly."""
    centre = round(round(spot / step) * step, 4)
    return [round(centre + step * i, 4) for i in range(-L.HALF_WIN, L.HALF_WIN + 1)]


L.window_strikes = _window


def strike_step(entry):
    """Strike spacing near spot: the most common gap among the ~12 listed strikes closest to spot, in the
    first expiry (weeklies are the finest). Falls back to 1."""
    spot = entry.get("spot") or 0
    exps = entry.get("exps") or []
    if not exps or not spot:
        return 1.0
    ks = sorted(exps[0][1].keys(), key=lambda k: abs(k - spot))[:12]
    ks = sorted(ks)
    gaps = [round(b - a, 4) for a, b in zip(ks, ks[1:]) if b - a > 1e-6]
    if not gaps:
        return 1.0
    c = collections.Counter(gaps).most_common()
    best = max(n for _g, n in c)
    return min(g for g, n in c if n == best)      # ties: the finer spacing


# ---------------------------------------------------------------- minute log + tail per name
def flow_path(sym, day):
    return os.path.join(DATA, "focus_flow_%s_%s.ndjson" % (sym, day))


def write_tail(sym, day, path, state):
    size = os.path.getsize(path)
    sz = state.setdefault("flow_size", {})
    gen = state.setdefault("flow_gen", {})
    sz[sym] = size
    start = max(0, size - TAIL_WINDOW)
    with open(path, "rb") as fh:
        fh.seek(start)
        blob = fh.read(size - start)
    off0 = start
    if start > 0:
        cut = blob.find(b"\n")
        off0, blob = (start + cut + 1, blob[cut + 1:]) if cut >= 0 else (size, b"")
    blob = blob[: blob.rfind(b"\n") + 1]
    end = off0 + len(blob)
    text = b"".join(r + b"\n" for r in blob.split(b"\n")[:-1][-TAIL_LINES:])
    L.atomic_write(os.path.join(DATA, "focus_tail_%s.json" % sym),
                   {"day": day, "sym": sym, "gen": int(gen.get(sym, 0)), "size": end, "from": end - len(text),
                    "text": text.decode("ascii", "replace"), "ts": L.now_et().strftime("%Y-%m-%dT%H:%M:%S")})


# ---------------------------------------------------------------- database (best effort, never blocks the page files)
_DB = {}


def to_db(dec, syms, today, ts_s):
    try:
        from trading_intel.focus import feed
        from trading_intel.timeutils import eastern_now
        if "sf" not in _DB:
            from trading_intel.config import get_settings
            from trading_intel.memory.db import make_session_factory
            _DB["sf"] = make_session_factory(get_settings())
        ts = datetime.datetime.fromisoformat(ts_s)
        chain, snaps = feed.records(dec, syms, today, ts, eastern_now())
        with _DB["sf"]() as s:
            n1, n2 = feed.write(s, chain, snaps)
        return n1, n2
    except Exception as exc:
        log("db: %s" % str(exc)[:160])
        return None


def day_open(sym, today):
    """The 09:30 open from bars_1m (the chain bank's 1-minute bars), for a name added after the open - otherwise
    its first live spot would be shown as "the open". None when not there yet (first spot is used, as before)."""
    try:
        from sqlalchemy import select
        from trading_intel.memory.models_flow import Bars1m
        if "sf" not in _DB:
            from trading_intel.config import get_settings
            from trading_intel.memory.db import make_session_factory
            _DB["sf"] = make_session_factory(get_settings())
        t0 = datetime.datetime.combine(today, datetime.time(9, 30))
        with _DB["sf"]() as s:
            v = s.execute(select(Bars1m.open).where(Bars1m.symbol == sym, Bars1m.ts >= t0,
                                                    Bars1m.ts < t0 + datetime.timedelta(minutes=5))
                          .order_by(Bars1m.ts).limit(1)).scalar_one_or_none()
        return float(v) if v else None
    except Exception as exc:
        log("open %s: %s" % (sym, str(exc)[:100]))
        return None


# ---------------------------------------------------------------- one pass
def process(db=True):
    day = L.now_et().strftime("%Y%m%d")
    raw = os.path.join(DATA, "focus_raw_%s.ndjson.gz" % day)
    if not os.path.exists(raw):
        return 0
    spath = os.path.join(DATA, "focus_state_%s.json" % day)
    try:
        state = json.load(open(spath, encoding="utf-8"))
    except Exception:
        state = {}
    done = int(state.get("lines_done", 0))
    off, line0 = int(state.get("raw_off", 0)), int(state.get("raw_line", 0))
    if line0 > done:
        off, line0 = 0, 0
    lines, new_off, new_line = rawio.read_lines(raw, off, line0)
    state["raw_off"], state["raw_line"] = new_off, new_line
    today = L.today_et()
    live = None
    n = 0
    touched = set()
    for i, line in lines:
        if i < done:
            continue
        state["lines_done"] = i + 1
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("pull") != "focus":
            continue
        try:
            dec = L.decode(rec)
        except Exception:
            log("decode failed on line %d" % i)
            continue
        ts = rec.get("ts") or ""
        live = {"ts": ts, "syms": [], "_source": "convex_focus"}
        for sym in rec.get("symbols") or []:
            if sym not in dec:
                continue
            L.STEP[sym] = strike_step(dec[sym])
            if db and sym not in state.get("open", {}) and ts[11:16] >= "09:31":   # added mid-session
                o = day_open(sym, today)
                if o:
                    state.setdefault("open", {})[sym] = o
            try:
                sd, fl = L.build_symbol(sym, dec, today, state, ts)
            except Exception as exc:
                log("%s: %s" % (sym, str(exc)[:120]))
                continue
            if not sd:
                continue
            live[sym] = sd
            live["syms"].append(sym)
            if fl and fl["t"] <= "16:00":
                p = flow_path(sym, day)
                cur = os.path.getsize(p) if os.path.exists(p) else 0
                if sym in state.get("flow_size", {}) and cur != state["flow_size"][sym]:
                    state.setdefault("flow_gen", {})[sym] = int(state["flow_gen"].get(sym, 0)) + 1
                with open(p, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(fl, separators=(",", ":")) + "\n")
                state.setdefault("flow_size", {})[sym] = os.path.getsize(p)
                touched.add(sym)
        if db and live["syms"]:
            first = [s for s in live["syms"] if s not in state.setdefault("db_seen", [])]
            if first or ts[14:16].isdigit() and int(ts[14:16]) % DB_EVERY_MIN == 0:
                r = to_db(dec, live["syms"], today, ts)
                if r is not None:
                    state["db_seen"] = sorted(set(state["db_seen"]) | set(live["syms"]))
                    state["db_last"] = {"ts": ts, "chain_rows": r[0], "snapshots": r[1]}
        n += 1
    for sym in touched:
        try:
            write_tail(sym, day, flow_path(sym, day), state)
        except Exception as exc:
            log("tail %s: %s" % (sym, str(exc)[:80]))
    if live:
        live["db"] = state.get("db_last")
        L.atomic_write(os.path.join(DATA, "focus_live.json"), live)
    L.atomic_write(spath, state)
    return n


def main():
    os.makedirs(DATA, exist_ok=True)
    once = "--once" in sys.argv
    log("focus writer start once=%s" % once)
    while True:
        n_et = L.now_et()
        if not once and (n_et.weekday() >= 5 or n_et.hour >= END_HH):
            log("window closed - exiting")
            break
        try:
            n = process()
            if n:
                log("pass: %d focus pull(s)" % n)
        except Exception:
            log("ERROR\n" + traceback.format_exc())
        if once:
            break
        time.sleep(20)


if __name__ == "__main__":
    main()
