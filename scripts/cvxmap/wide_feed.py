"""
wide_feed.py - created 2026-11-09 for Mithil. Feeds the Orbs + Volume profile page (flow_orbs.html).

WHAT IT DOES
  Reads the collector's raw pulls (data/raw_YYYYMMDD.ndjson.gz, pull "idx": SPX / SPY / QQQ, 12 expiries,
  +-3.5% of spot, every minute - already banked, so this costs ZERO extra Convex calls) and keeps a WIDER
  slice than the cockpit's minute log does:
      SPX  +-200 points (81 strikes)         SPY / QQQ  +-2.7% of spot (same width as SPX +-200)
      every expiry up to 7 calendar days out (0DTE kept apart from 1-7 DTE)
  Writes data/wide_YYYYMMDD.json (2026-11-09 v2: ONE block per collector pull = every minute, compacted - see
  "DAY FILE v2" below) and data/wide_index.json (the list of days), and copies flow_orbs.html into data/ so the
  existing server shows it at  http://192.168.1.211:8899/flow_orbs.html
  2026-11-09 (cockpit Orbs tab): also data/wide_live_<SYM>.json = the newest minute only (full block, ~10 KB),
  written the moment a new pull is read (the live loop looks for new pulls every POLL_S = 3 s), so the cockpit
  polls a small file every 3 s and loads the day file once. And data/vixmap_YYYYMMDD.json: the VIX tab's dealer
  gamma / vanna map from the collector's VIX pull (vix_map.py) - same raw file, no extra vendor calls.

DAY FILE v2 (2026-11-09, Mithil: 1-minute candles/blocks; the page aggregates 1 / 3 / 5 min)
  Every slot = one pull. Strikes = k0 + i * step (n of them). Fields g / gv / gt / va are that minute's
  snapshot, stored as whole units of $10k (GQ = 1e-5 $bn). Fields
  c p cb cs pb ps (contracts) and cP pP (premium) are CUMULATIVE for the day; in the day file they are stored as
  the change since the previous slot of the same symbol, matched by strike (a strike new to the window carries
  its full value), premium in whole $k. "dz": 1 marks such a slot; decode_slots() / the pages' decoder rebuild
  the cumulative values exactly. "ex" (expiries + IV) is kept on the newest slot only. The live file is never
  delta-coded.

WHAT IT NEVER TOUCHES
  The Map, live.json, flow_*.ndjson, cvx_cockpit.html, cvx_live.py, run_cvxmap.sh and every other production
  file are unchanged. It only IMPORTS cvx_live's helpers (decode, parity forward, implied vol) so the numbers
  use the writer's own conventions.

THE MATHS (same per-strike formulas as cvx_live.tos_rows - checked by tests/cvxmap/test_wide_feed.py)
  One flat IV per expiry (the call IV at the centre strike, as the writer does), Black-Scholes r = 0.04.
  0DTE time = real minutes to the 16:15 settlement; longer expiries = max(days, 0.25) / 365.
  Dealer gamma $bn per 1% move, three ways (the page's "Basis" switch):
      OI      = gamma x (call OI - put OI)            the writer's own naive sign: dealers long calls, short puts
      Volume  = gamma x (call vol - put vol)          the writer's gexV
      Trades  = - gamma x (calls bought - calls sold + puts bought - puts sold)
                                                      measured: whoever bought, the dealer sold (Convex trade tags)
  Vanna $bn per 1 vol point = vanna x (call OI - put OI) (writer's sign).
  Volume profile = today's cumulative contracts per strike (calls / puts, bought / sold by trade tag).

RUN
  python scripts/cvxmap/wide_feed.py                  live loop (09:25-16:20 ET, exits by itself)
  python scripts/cvxmap/wide_feed.py --date 20261106  rebuild one stored day from its raw file, then exit
  NAS:  sudo bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_wide.sh start | stop | status | day YYYYMMDD...
"""
import os, sys, json, math, time, zlib, shutil, datetime, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cvx_live as L   # helpers only; its main() never runs on import

DATA = os.path.join(HERE, "data")
PAGE = "flow_orbs.html"
SYMS = ("SPX", "SPY", "QQQ")
WIDE_PTS_SPX = 200.0            # +-200 SPX points
WIDE_FRAC = WIDE_PTS_SPX / 7400.0   # ~2.7%: the same width for SPY / QQQ
MAX_DTE = 7                     # calendar days
SLOT_MIN = 1                    # 2026-11-09: one stored block per pull (the idx pull is every 60 s)
START_HM, END_HM = (9, 25), (16, 20)
POLL_S = 3                      # 2026-11-09: look for a new pull every 3 s (reads only new gzip members)
CUM_INT = ("c", "p", "cb", "cs", "pb", "ps")     # cumulative contracts
CUM_PREM = ("cP", "pP")                         # cumulative premium, $M (stored as whole $k in the day file)
SNAP = ("g", "gv", "gt", "va")                  # per-minute snapshot greeks
LIVE_MAX_AGE_S = 150            # a pull older than this (catch-up after a late start) does not write live files
WANT = (b'"idx"', b'"vix"')     # cheap pre-filter; feed() checks the pull name
try:
    import vix_map                                  # VIX tab map (2026-11-09); the Orbs feed runs without it
except Exception:                                   # pragma: no cover
    vix_map = None


def log(msg):
    print("%s wide_feed  %s" % (L.now_et().strftime("%H:%M:%S"), msg), flush=True)


def _hm(ts):
    return str(ts)[11:16]


def _r(x, d=4):
    """Round to d significant-ish decimals; keeps the file small."""
    if x is None:
        return None
    if x == 0:
        return 0
    return round(x, d)


def half_steps(sym, spot):
    step = L.STEP.get(sym, 1.0)
    if sym == "SPX":
        return int(round(WIDE_PTS_SPX / step))
    return max(5, int(round(WIDE_FRAC * spot / step)))


def strikes(sym, spot):
    step = L.STEP.get(sym, 1.0)
    c = round(spot / step) * step
    n = half_steps(sym, spot)
    return [c + step * i for i in range(-n, n + 1)]


def greeks_u(spot, k, T, sigma):
    """gamma_u, vanna_u exactly as cvx_live.tos_rows computes them."""
    if not (sigma > 0 and T > 0 and k > 0 and spot > 0):
        return 0.0, 0.0
    sq = sigma * math.sqrt(T)
    eqT = math.exp(-L.Q_YIELD * T)
    d1 = (math.log(spot / k) + (L.R_RATE - L.Q_YIELD + sigma * sigma / 2.0) * T) / sq
    d2 = d1 - sq
    npd1 = math.exp(-d1 * d1 / 2.0) / L.SQ2PI
    gamma_u = eqT * npd1 / (spot * sigma * math.sqrt(T))
    vanna_u = -eqT * npd1 * d2 / sigma
    return gamma_u, vanna_u


def expiry_books(sym, d, today, now):
    """[(group, dte, T_greeks, sigma, book)] for every expiry <= MAX_DTE. group '0' = today's PM expiry, '7' = the rest."""
    out = []
    spot = d["spot"]
    sym_groups = [("PM", g) for g in L.pm_groups(d)] + [("AM", g) for g in L.am_groups(d)]
    first_pm = True
    for fam, (head, bk) in sym_groups:
        ed = L.exp_date(head)
        try:
            dte = (datetime.date.fromisoformat(ed) - today).days
        except Exception:
            continue
        if dte < 0 or dte > MAX_DTE:
            continue
        if fam == "AM" and dte < 1:          # AM monthly settled at today's open
            continue
        mins = L.minutes_to_close(now)
        T = (mins / 525600.0) if dte == 0 else max(dte, L.MIN_T_DAYS) / 365.0
        Tiv = (mins / 525600.0) if dte == 0 else dte / 365.0
        F, _ = L.parity_forward([(k, c, p) for k, (c, p) in bk.items()], spot)
        sigma, _c = L.atm_iv(spot, L.STEP.get(sym, 1.0), bk, Tiv, F)
        grp = "0" if (fam == "PM" and dte == 0 and first_pm) else "7"
        if fam == "PM" and dte == 0:
            first_pm = False
        out.append((grp, dte, T, sigma, bk, ed))
    return out


FIELDS = ("g", "gv", "gt", "va", "c", "p", "cb", "cs", "pb", "ps", "cP", "pP")


def build_slot(sym, d, today, now):
    """One block for one symbol at one minute: per strike, 0DTE and 1-7 DTE kept apart."""
    spot = d["spot"]
    ks = strikes(sym, spot)
    acc = {g: {f: [0.0] * len(ks) for f in FIELDS} for g in ("0", "7")}
    seen = {"0": [], "7": []}
    for grp, dte, T, sigma, bk, ed in expiry_books(sym, d, today, now):
        seen[grp].append({"e": ed, "dte": dte, "iv": _r(sigma, 4)})
        A = acc[grp]
        for i, k in enumerate(ks):
            cp = bk.get(k)
            if not cp:
                continue
            c, p = cp
            gu, vu = greeks_u(spot, k, T, sigma)
            gk = 100.0 * spot * spot * 0.01 / L.B
            cOI, pOI = L._n(c.get("oi")), L._n(p.get("oi"))
            cV, pV = L._n(c.get("volm")), L._n(p.get("volm"))
            cb, cs = L._n(c.get("volm_buy")), L._n(c.get("volm_sell"))
            pb, ps = L._n(p.get("volm_buy")), L._n(p.get("volm_sell"))
            A["g"][i] += gu * (cOI - pOI) * gk
            A["gv"][i] += gu * (cV - pV) * gk
            A["gt"][i] += -gu * ((cb - cs) + (pb - ps)) * gk
            A["va"][i] += vu * (cOI - pOI) * 100.0 * spot * 0.01 / L.B
            A["c"][i] += cV
            A["p"][i] += pV
            A["cb"][i] += cb
            A["cs"][i] += cs
            A["pb"][i] += pb
            A["ps"][i] += ps
            cm, pm = L.mid(c) or 0.0, L.mid(p) or 0.0
            A["cP"][i] += cV * cm * 100.0 / 1e6
            A["pP"][i] += pV * pm * 100.0 / 1e6
    blk = {"t": _hm(now.isoformat()), "x": round(spot, 2), "k": ks, "ex": seen}
    for g in ("0", "7"):
        for f in FIELDS:
            dp = 6 if f in ("g", "gv", "gt", "va") else (3 if f in ("cP", "pP") else 0)
            blk[f + g] = [(_r(v, dp) if dp else int(round(v))) for v in acc[g][f]]
    return blk


# ---------------------------------------------------------------- raw file reading (members, no re-read)
class RawReader:
    """Each collector write is its own gzip member, so we remember the byte offset of the last complete
    member and decompress only what is new. A half-written member is left for the next pass."""

    def __init__(self, path):
        self.path, self.off = path, 0

    CHUNK = 1 << 18

    def new_records(self):
        return list(self.iter_records())

    def iter_records(self, want=None):
        """Stream the file member by member (2026-11-09: the old version read the whole day into one buffer
        and kept every record of every symbol, which pushed the NAS into swap - ~25 min per day).
        `want` = bytes (or a tuple of bytes, any of them) that must appear in a line before it is JSON-parsed."""
        if isinstance(want, bytes):
            want = (want,)
        if not os.path.exists(self.path):
            return
        with open(self.path, "rb") as fh:
            fh.seek(self.off)
            dz, parts, pending = zlib.decompressobj(31), [], 0
            while True:
                data = fh.read(self.CHUNK)
                if not data:
                    break
                while data:
                    try:
                        parts.append(dz.decompress(data))
                    except zlib.error:
                        return
                    if dz.eof:                 # one member (= one collector write) complete
                        rest = dz.unused_data
                        self.off += pending + len(data) - len(rest)
                        pending = 0
                        for line in b"".join(parts).splitlines():
                            if not line.strip() or (want and not any(w in line for w in want)):
                                continue
                            try:
                                yield json.loads(line)
                            except Exception:
                                pass
                        dz, parts, data = zlib.decompressobj(31), [], rest
                    else:
                        pending += len(data)
                        data = b""
        # a half-written member at the end is left (offset not advanced) and read again next pass


# ---------------------------------------------------------------- day file v2 coding
GQ = 1e-5                       # day file: greeks stored as whole units of 1e-5 $bn ($10k)


def encode_slots(slots):
    """Full blocks -> day-file v2 blocks: strikes as k0 + n (step = the symbol's), greeks as whole $10k,
    cumulative fields as per-strike changes since the previous slot (premium in whole $k)."""
    out, prev = [], {}
    for j, blk in enumerate(slots):
        ks = blk["k"]
        e = {"t": blk["t"], "x": blk["x"], "k0": ks[0], "n": len(ks), "dz": 1}
        if j == len(slots) - 1 and "ex" in blk:
            e["ex"] = blk["ex"]
        for g in ("0", "7"):
            for f in SNAP:
                e[f + g] = [int(round((v or 0) / GQ)) for v in blk[f + g]]
            for f in CUM_INT + CUM_PREM:
                cur = blk[f + g]
                if f in CUM_PREM:
                    cur = [int(round((v or 0) * 1000)) for v in cur]
                pm = prev.get(f + g, {})
                e[f + g] = [int(v) - pm.get(k, 0) for k, v in zip(ks, cur, strict=False)]
                prev[f + g] = dict(zip(ks, (int(v) for v in cur), strict=False))
        out.append(e)
    return out


def decode_slots(slots, step):
    """Inverse of encode_slots (greeks to the nearest $10k) - the pages do the same in JS."""
    out, prev = [], {}
    for e in slots:
        if not e.get("dz"):
            out.append(e)
            continue
        ks = [e["k0"] + step * i for i in range(e["n"])]
        blk = {k: v for k, v in e.items() if k not in ("dz", "k0", "n")}
        blk["k"] = ks
        for g in ("0", "7"):
            for f in SNAP:
                blk[f + g] = [v * GQ for v in e[f + g]]
            for f in CUM_INT + CUM_PREM:
                pm = prev.get(f + g, {})
                cur = [pm.get(k, 0) + d for k, d in zip(ks, e[f + g], strict=False)]
                prev[f + g] = dict(zip(ks, cur, strict=False))
                blk[f + g] = [v / 1000.0 for v in cur] if f in CUM_PREM else cur
        out.append(blk)
    return out


# ---------------------------------------------------------------- day state
class Day:
    def __init__(self, ymd, live_mode=False):
        self.ymd = ymd
        self.today = datetime.date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:]))
        self.slots = {s: [] for s in SYMS}     # one block per pull
        self.px = {s: [] for s in SYMS}        # every minute's spot [t, x]
        self.reader = RawReader(os.path.join(DATA, "raw_%s.ndjson.gz" % ymd))
        self.n_recs = 0
        self.live_mode = live_mode
        self.live_writes = 0
        self.vix = vix_map.VixDay(ymd) if vix_map is not None else None
        self.dirty = False

    def feed(self, rec):
        pull = rec.get("pull")
        if pull not in ("idx", "vix"):
            return False
        try:
            now = datetime.datetime.fromisoformat(str(rec.get("ts"))[:19])
        except Exception:
            return False
        t = now.strftime("%H:%M")
        if t < "09:30" or t > "16:15":
            return False
        if pull == "vix":
            return self.feed_vix(rec, now)
        dec = L.decode(rec)
        got = []
        for s in SYMS:
            d = dec.get(s)
            if not d or not d.get("exps") or not d.get("spot"):
                continue
            try:
                blk = build_slot(s, d, self.today, now)
            except Exception as exc:
                log("%s %s: %s" % (s, t, str(exc)[:120]))
                continue
            sl, px = self.slots[s], self.px[s]
            if sl and sl[-1]["t"] == t:
                sl[-1] = blk
            else:
                sl.append(blk)
            if px and px[-1][0] == t:
                px[-1] = [t, round(d["spot"], 2)]
            else:
                px.append([t, round(d["spot"], 2)])
            got.append(s)
        self.dirty = self.dirty or bool(got)
        if got and self.live_mode and (L.now_et() - now).total_seconds() < LIVE_MAX_AGE_S:
            for s in got:
                self.write_live(s)      # the newest minute goes out before anything else is done
        self.n_recs += 1
        return True

    def feed_vix(self, rec, now):
        if self.vix is None:
            return False
        try:
            dv = L.decode(rec).get("VIX")
            return bool(dv) and self.vix.feed(dv, now)
        except Exception as exc:
            log("vix map %s: %s" % (now.strftime("%H:%M"), str(exc)[:120]))
            return False

    def pump(self):
        n = 0
        for rec in self.reader.iter_records(want=WANT):
            if self.feed(rec):
                n += 1
        return n

    def live_payload(self, s):
        sl = self.slots[s]
        if not sl:
            return None
        return {"v": 2, "day": self.today.isoformat(), "sym": s, "step": L.STEP.get(s, 1.0),
                "gen": L.now_et().isoformat(timespec="seconds"), "slot_min": SLOT_MIN,
                "n": len(sl), "slot": sl[-1], "px": self.px[s][-15:]}

    def write_live(self, s):
        p = self.live_payload(s)
        if p is not None:
            L.atomic_write(os.path.join(DATA, "wide_live_%s.json" % s), p)
            self.live_writes += 1

    def payload(self):
        syms = {}
        for s in SYMS:
            sl = self.slots[s]
            if not sl:
                continue
            syms[s] = {"step": L.STEP.get(s, 1.0), "slots": encode_slots(sl), "px": self.px[s]}
        asof = max((v[-1][0] for v in self.px.values() if v), default="")
        return {"v": 2, "day": self.today.isoformat(), "asof": asof,
                "gen": L.now_et().isoformat(timespec="seconds"), "max_dte": MAX_DTE, "slot_min": SLOT_MIN,
                "enc": "dz1: c p cb cs pb ps cP($k) pP($k) as per-strike change since the previous slot",
                "width": {"SPX": "+-%d pts" % WIDE_PTS_SPX, "other": "+-%.1f%%" % (WIDE_FRAC * 100)},
                "syms": syms}

    def write(self):
        if self.dirty and any(self.slots.values()):
            self.dirty = False
            L.atomic_write(os.path.join(DATA, "wide_%s.json" % self.ymd), self.payload())
            write_index()
        if self.vix is not None and self.vix.changed:
            L.atomic_write(os.path.join(DATA, "vixmap_%s.json" % self.ymd), self.vix.payload())
            self.vix.changed = False


def write_index():
    days = sorted((f[5:13] for f in os.listdir(DATA) if f.startswith("wide_2") and f.endswith(".json")), reverse=True)
    L.atomic_write(os.path.join(DATA, "wide_index.json"), {"days": days, "gen": L.now_et().isoformat(timespec="seconds")})


def copy_page():
    src = os.path.join(HERE, PAGE)
    if os.path.exists(src):
        try:
            shutil.copyfile(src, os.path.join(DATA, PAGE))
        except Exception as exc:
            log("page copy failed: %s" % exc)


def rebuild(ymd):
    t0 = time.time()
    day = Day(ymd)
    n = day.pump()
    if not any(day.slots.values()):
        log("%s: no idx pulls in raw_%s.ndjson.gz" % (ymd, ymd))
        return 1
    day.write()
    sz = os.path.getsize(os.path.join(DATA, "wide_%s.json" % ymd))
    log("%s: %d pulls -> wide_%s.json  %.1f MB  %.0fs" % (ymd, n, ymd, sz / 1e6, time.time() - t0))
    return 0


def live():
    copy_page()
    ymd = L.now_et().strftime("%Y%m%d")
    day = Day(ymd, live_mode=True)
    log("live start %s  (poll %ds)" % (ymd, POLL_S))
    while True:
        now = L.now_et()
        hm = (now.hour, now.minute)
        if now.weekday() >= 5 or hm > END_HM:
            log("window closed - exiting (%d pulls)" % day.n_recs)
            break
        try:
            n = day.pump()
            if n:
                day.write()
                log("pass: %d new pull(s)" % n)
        except Exception:
            log("ERROR\n" + traceback.format_exc())
        time.sleep(POLL_S)


def main():
    os.makedirs(DATA, exist_ok=True)
    if "--date" in sys.argv:
        copy_page()
        days = [a for a in sys.argv[sys.argv.index("--date") + 1:] if a.isdigit() and len(a) == 8]
        rc = 0
        for ymd in days:
            try:
                rc |= rebuild(ymd)
            except Exception:
                log("%s FAILED\n%s" % (ymd, traceback.format_exc()))
                rc = 1
        sys.exit(rc)
    if "--page" in sys.argv:
        copy_page()
        write_index()
        return
    live()


if __name__ == "__main__":
    main()
