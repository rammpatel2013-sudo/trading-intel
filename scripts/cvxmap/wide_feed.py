"""
wide_feed.py - created 2026-11-09 for Mithil. Feeds the Orbs + Volume profile page (flow_orbs.html).

WHAT IT DOES
  Reads the collector's raw pulls (data/raw_YYYYMMDD.ndjson.gz, pull "idx": SPX / SPY / QQQ, 12 expiries,
  +-3.5% of spot, every minute - already banked, so this costs ZERO extra Convex calls) and keeps a WIDER
  slice than the cockpit's minute log does:
      SPX  +-200 points (81 strikes)         SPY / QQQ  +-2.7% of spot (same width as SPX +-200)
      every expiry up to 7 calendar days out (0DTE kept apart from 1-7 DTE)
  Writes data/wide_YYYYMMDD.json (one block every 5 minutes + the newest minute) and data/wide_index.json
  (the list of days), and copies flow_orbs.html into data/ so the existing server shows it at
      http://192.168.1.211:8899/flow_orbs.html

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
SLOT_MIN = 5                    # one stored block every 5 minutes
START_HM, END_HM = (9, 25), (16, 20)
POLL_S = 20


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
        if not os.path.exists(self.path):
            return []
        with open(self.path, "rb") as fh:
            fh.seek(self.off)
            buf = fh.read()
        mv, out = memoryview(buf), []
        i = done = 0
        dz, parts = zlib.decompressobj(31), []
        while i < len(buf):
            piece = mv[i:i + self.CHUNK]
            try:
                parts.append(dz.decompress(piece))
            except zlib.error:
                break
            if dz.eof:                         # one member (= one collector write) complete
                i += len(piece) - len(dz.unused_data)
                done = i
                for line in b"".join(parts).decode("utf-8", "replace").splitlines():
                    line = line.strip()
                    if line:
                        try:
                            out.append(json.loads(line))
                        except Exception:
                            pass
                dz, parts = zlib.decompressobj(31), []
            else:
                i += len(piece)
        self.off += done                       # a half-written member is read again next pass
        return out


# ---------------------------------------------------------------- day state
class Day:
    def __init__(self, ymd):
        self.ymd = ymd
        self.today = datetime.date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:]))
        self.slots = {s: [] for s in SYMS}     # finished 5-min blocks
        self.last = {s: None for s in SYMS}    # newest minute's block (provisional)
        self.px = {s: [] for s in SYMS}        # every minute's spot [t, x]
        self.reader = RawReader(os.path.join(DATA, "raw_%s.ndjson.gz" % ymd))
        self.n_recs = 0

    def feed(self, rec):
        if rec.get("pull") != "idx":
            return False
        try:
            now = datetime.datetime.fromisoformat(str(rec.get("ts"))[:19])
        except Exception:
            return False
        t = now.strftime("%H:%M")
        if t < "09:30" or t > "16:15":
            return False
        dec = L.decode(rec)
        for s in SYMS:
            d = dec.get(s)
            if not d or not d.get("exps") or not d.get("spot"):
                continue
            try:
                blk = build_slot(s, d, self.today, now)
            except Exception as exc:
                log("%s %s: %s" % (s, t, str(exc)[:120]))
                continue
            self.px[s].append([t, round(d["spot"], 2)])
            mins = now.hour * 60 + now.minute
            if mins % SLOT_MIN == 0 or not self.slots[s]:
                if self.slots[s] and self.slots[s][-1]["t"] == t:
                    self.slots[s][-1] = blk
                else:
                    self.slots[s].append(blk)
                self.last[s] = None
            else:
                self.last[s] = blk
        self.n_recs += 1
        return True

    def pump(self):
        n = 0
        for rec in self.reader.new_records():
            if self.feed(rec):
                n += 1
        return n

    def payload(self):
        syms = {}
        for s in SYMS:
            sl = list(self.slots[s])
            if self.last[s] is not None:
                sl.append(dict(self.last[s], live=1))
            if not sl:
                continue
            syms[s] = {"step": L.STEP.get(s, 1.0), "slots": sl, "px": self.px[s]}
        asof = max((v[-1][0] for v in self.px.values() if v), default="")
        return {"v": 1, "day": self.today.isoformat(), "asof": asof,
                "gen": L.now_et().isoformat(timespec="seconds"), "max_dte": MAX_DTE, "slot_min": SLOT_MIN,
                "width": {"SPX": "+-%d pts" % WIDE_PTS_SPX, "other": "+-%.1f%%" % (WIDE_FRAC * 100)},
                "syms": syms}

    def write(self):
        L.atomic_write(os.path.join(DATA, "wide_%s.json" % self.ymd), self.payload())
        write_index()


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
    if not n:
        log("%s: no idx pulls in raw_%s.ndjson.gz" % (ymd, ymd))
        return 1
    day.write()
    sz = os.path.getsize(os.path.join(DATA, "wide_%s.json" % ymd))
    log("%s: %d pulls -> wide_%s.json  %.1f MB  %.0fs" % (ymd, n, ymd, sz / 1e6, time.time() - t0))
    return 0


def live():
    copy_page()
    ymd = L.now_et().strftime("%Y%m%d")
    day = Day(ymd)
    log("live start %s" % ymd)
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
