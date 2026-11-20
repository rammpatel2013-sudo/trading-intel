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
import collections, datetime, json, math, os, sys, time, traceback

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



# ---------------------------------------------------------------- 0-7 DTE levels (Mithil 04:32: "flow vol gamma n vanna level.
# charm will not play role if not 0DTE. call wall put wall and flip zero line matters")
NEAR_DTE = 7
_SQ2PI = math.sqrt(2.0 * math.pi)


def _years(exp, now):
    """Calendar time to the 16:00 close of expiry day, in years (floor 1 minute)."""
    end = datetime.datetime.combine(exp, datetime.time(16, 0))
    return max((end - now).total_seconds(), 60.0) / (365.0 * 86400.0)


def book_0_7(entry, now):
    """Every contract of the expiries within 7 days: strike, OTM implied vol (parity forward, mids), call / put OI,
    time to expiry. The same inputs the cockpit's maps use, across 0-7 DTE instead of the nearest expiry only."""
    spot = entry.get("spot") or 0
    out = []
    if not spot:
        return out
    for head, book in entry.get("exps") or []:
        try:
            exp = datetime.date.fromisoformat(L.exp_date(head))
        except Exception:
            continue
        dte = (exp - now.date()).days
        if dte < 0 or dte > NEAR_DTE:
            continue
        T = _years(exp, now)
        F, _sd = L.parity_forward([(k, c, p) for k, (c, p) in book.items()], spot, band=spot * 0.10)
        for k, (c, p) in book.items():
            civ = L.implied(L.mid(c), F, k, T, "C")
            piv = L.implied(L.mid(p), F, k, T, "P")
            sg = (civ if k >= F else piv) or civ or piv
            if not sg:
                continue
            out.append({"k": k, "sg": sg, "cIV": civ or 0, "pIV": piv or 0, "cOI": L._n(c.get("oi")),
                        "pOI": L._n(p.get("oi")), "cV": L._n(c.get("volm")), "pV": L._n(p.get("volm")),
                        "T": T, "dte": dte, "e": exp.isoformat()})
    return out


def _greeks(S, K, sg, T):
    sq = sg * math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sg * sg * T) / sq
    n = math.exp(-d1 * d1 / 2.0) / _SQ2PI
    gamma = n / (S * sq)
    vanna = -n * (d1 - sq) / sg
    return gamma, vanna


def net_at(rows, S):
    """Dealer gamma and vanna at price S (cockpit's naive sign: (call OI - put OI), so a call-heavy strike = dealers
    long gamma there), $ per 1% move / per 1 vol point, in billions like the map."""
    g = v = 0.0
    for r in rows:
        ga, va = _greeks(S, r["k"], r["sg"], r["T"])
        net = r["cOI"] - r["pOI"]
        g += ga * net * 100.0 * S * S * 0.01
        v += va * net * 100.0 * S * 0.01
    return g / 1e9, v / 1e9


def _zero(xs, ys, S):
    best = None
    for (x0, y0), (x1, y1) in zip(zip(xs, ys), zip(xs[1:], ys[1:])):
        if (y0 < 0 <= y1) or (y0 > 0 >= y1):
            k = x0 + (x1 - x0) * (0 - y0) / ((y1 - y0) or 1e-12)
            if best is None or abs(k - S) < abs(best - S):
                best = k
    return best


def levels(rows, S):
    """Call wall (most call OI at/above spot), put wall (most put OI at/below), gamma flip and vanna flip (the zero
    crossing nearest spot on a +-15% grid), dealer gamma / vanna now, by expiry and all 0-7 DTE together."""
    if not rows or not S:
        return None
    co, po = {}, {}
    for r in rows:
        co[r["k"]] = co.get(r["k"], 0.0) + r["cOI"]
        po[r["k"]] = po.get(r["k"], 0.0) + r["pOI"]
    cw = max((k for k in co if k >= S), key=lambda k: co[k], default=None)
    pw = max((k for k in po if k <= S), key=lambda k: po[k], default=None)
    xs = [S * (0.85 + 0.0025 * i) for i in range(121)]
    gv = [net_at(rows, x) for x in xs]
    g0, v0 = net_at(rows, S)
    out = {"call_wall": cw, "put_wall": pw, "gamma_flip": _zero(xs, [a for a, _b in gv], S),
           "vanna_flip": _zero(xs, [b for _a, b in gv], S), "gamma_now": g0, "vanna_now": v0,
           "expiries": sorted({r["e"] for r in rows}), "n": len(rows)}
    # biggest single-strike dealer vanna in the window (a level where vol moves push hedging hardest)
    best = max(rows, key=lambda r: abs(_greeks(S, r["k"], r["sg"], r["T"])[1] * (r["cOI"] - r["pOI"])), default=None)
    out["vanna_strike"] = best["k"] if best else None
    return out


def near_book(rows, first_exp):
    """The cockpit's 1-7 DTE book shape (ti.json near_book rows) for the expiries after the nearest one, so the Map's
    Week / All books include them with their own time to expiry."""
    return {"rows": [{"k": r["k"], "cIV": r["cIV"], "pIV": r["pIV"], "cOI": r["cOI"], "pOI": r["pOI"], "dte": r["dte"],
                      "e": r["e"], "set": "PM", "exp_t": r["e"] + "T16:00"} for r in rows if r["e"] != first_exp]}


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



# ---------------------------------------------------------------- Orbs blocks (Mithil 04:55: Focus tab "gamma, vanna, or our
# orbs flow view along with technical 3 min chart with obv vwap volume profile"). wide_feed.build_slot UNCHANGED, so the
# Orbs maths are the index's; only the strike window is wider for single names (+-10% of spot instead of +-2.7%).
# Files (the Orbs tab and the Focus tab read them like the index's): wide_focus_YYYYMMDD.json (day, all picks) and
# wide_live_<SYM>.json (newest minute).
WIDE_FRAC_FOCUS = 0.10
_W = {}


def wide():
    if "W" not in _W:
        import wide_feed as W
        W.WIDE_FRAC = WIDE_FRAC_FOCUS
        _W["W"] = W
    return _W["W"]


ORBS = {"day": None, "slots": {}, "px": {}}


def orbs_load(day):
    """Resume the day's blocks from the file written so far (a restarted writer must not lose the morning)."""
    ORBS.update(day=(DATA, day), slots={}, px={})
    path = os.path.join(DATA, "wide_focus_%s.json" % day)
    try:
        j = json.load(open(path, encoding="utf-8"))
        for s, v in (j.get("syms") or {}).items():
            ORBS["slots"][s] = wide().decode_slots(v.get("slots") or [], v.get("step") or 1.0)
            ORBS["px"][s] = v.get("px") or []
    except Exception:
        pass


def orbs_add(sym, d, today, now, day):
    if ORBS["day"] != (DATA, day):
        orbs_load(day)
    W = wide()
    blk = W.build_slot(sym, d, today, now)
    t = now.strftime("%H:%M")
    sl = ORBS["slots"].setdefault(sym, [])
    px = ORBS["px"].setdefault(sym, [])
    if sl and sl[-1]["t"] == t:
        sl[-1] = blk
    else:
        sl.append(blk)
    if px and px[-1][0] == t:
        px[-1] = [t, round(d["spot"], 2)]
    else:
        px.append([t, round(d["spot"], 2)])
    L.atomic_write(os.path.join(DATA, "wide_live_%s.json" % sym),
                   {"v": 2, "day": today.isoformat(), "sym": sym, "step": L.STEP.get(sym, 1.0),
                    "gen": L.now_et().isoformat(timespec="seconds"), "slot_min": 1, "n": len(sl), "slot": sl[-1],
                    "px": px[-15:]})


def orbs_write(today, day):
    if not ORBS["slots"]:
        return
    W = wide()
    syms = {s: {"step": L.STEP.get(s, 1.0), "slots": W.encode_slots(sl), "px": ORBS["px"].get(s, [])}
            for s, sl in ORBS["slots"].items() if sl}
    L.atomic_write(os.path.join(DATA, "wide_focus_%s.json" % day),
                   {"v": 2, "day": today.isoformat(), "gen": L.now_et().isoformat(timespec="seconds"),
                    "max_dte": 7, "slot_min": 1, "focus": True, "syms": syms})


# ---------------------------------------------------------------- stock 1-minute bars for the Focus tab's price chart
# FMP historical-chart/1min through CVForge (the chain bank's own route), once a minute per pick -> bars_1m (owner
# table, source fmp, upsert) and focus_bars_<SYM>.json for the page. FMP's intraday bars can lag a few minutes.
BARS_EVERY_S = 60
_BARS = {"at": {}}


def bars_pull(syms, today):
    now = time.time()
    due = [s for s in syms if now - _BARS["at"].get(s, 0) >= BARS_EVERY_S]
    if not due:
        return
    try:
        from trading_intel.flow import bank
        if "cv" not in _BARS:
            from trading_intel.clients.cvforge import CVForgeClient
            from trading_intel.config import get_settings
            _BARS["cv"] = CVForgeClient(get_settings(), timeout=20.0)
        if "sf" not in _DB:
            from trading_intel.config import get_settings
            from trading_intel.memory.db import make_session_factory
            _DB["sf"] = make_session_factory(get_settings())
    except Exception as exc:
        log("bars setup: %s" % str(exc)[:120])
        for s in due:
            _BARS["at"][s] = now
        return
    for s in due:
        _BARS["at"][s] = now
        try:
            raw = _BARS["cv"].fmp("historical-chart/1min", {"symbol": s, "from": today.isoformat(), "to": today.isoformat()})
            rows = [r for r in bank.parse_fmp_1m(s, raw) if r["ts"].date() == today]
            if rows:
                with _DB["sf"]() as ses:
                    bank.save_bars_1m(ses, rows)
            rows.sort(key=lambda r: r["ts"])
            out = [[r["ts"].strftime("%H:%M"), r["open"], r["high"], r["low"], r["close"], r["volume"]] for r in rows
                   if "09:30" <= r["ts"].strftime("%H:%M") < "16:00"]
            L.atomic_write(os.path.join(DATA, "focus_bars_%s.json" % s),
                           {"day": today.isoformat(), "sym": s, "gen": L.now_et().isoformat(timespec="seconds"),
                            "src": "fmp 1-min via CVForge", "bars": out})
        except Exception as exc:
            log("bars %s: %s" % (s, str(exc)[:120]))


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
            try:
                pts = datetime.datetime.fromisoformat(ts) if ts else L.now_et()
                rows07 = book_0_7(dec[sym], pts)
                sd["lv"] = levels(rows07, sd["spot"])
                sd["near"] = near_book(rows07, sd.get("exp"))
            except Exception as exc:
                log("levels %s: %s" % (sym, str(exc)[:120]))
            try:
                orbs_add(sym, dec[sym], today, pts, day)
            except Exception as exc:
                log("orbs %s: %s" % (sym, str(exc)[:120]))
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
    if n:
        try:
            orbs_write(today, day)
        except Exception as exc:
            log("orbs write: %s" % str(exc)[:120])
    if live:
        state["syms_last"] = live["syms"]
        if db:
            bars_pull(live["syms"], today)
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
