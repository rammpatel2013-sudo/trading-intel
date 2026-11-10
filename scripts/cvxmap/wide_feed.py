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

BASES (2026-11-09: Orbs "Positions from" = the MAP's set OI | Vol | OI + vol | OI + signed)
  Every greek is stored on three dealer books, per strike and minute (snapshot fields, $bn):
      N_oi  = call OI - put OI                                       -> g   va   ch
      N_vol = call volume - put volume                               -> gv  vav  chv
      N_sig = (calls sold - calls bought) + (puts sold - puts bought) -> gt  vat  cht
              (trade tags: a customer buy leaves the dealer short, a sell leaves it long)
  The page adds them as the MAP's bookCP() builds its books (greeks are linear in position):
      OI = g · Vol = gv · OI + vol = g + gv · OI + signed = g + gt   (same for va / ch).
  OI + signed check: MAP call book = cOI + (cs - cb), put book = pOI + (pb - ps);
  dealer gamma = gamma x (call book - put book) = gamma x (cOI - pOI) + gamma x N_sig = g + gt.
  One difference: the MAP falls back to OI + vol for a strike with no buy/sell split;
  here a missing split counts as 0 (= OI only).
  CHARM ch / chv / cht = dealer delta change per calendar HOUR from time passing alone, $bn:
      charm_u = phi(d1) d2 / (2 T)   Black-Scholes, r = q = 0, T = calendar time
      (real minutes to 16:15 + whole days), the strike's own IV (out-of-the-money side vs the
      parity forward; the expiry's flat IV when missing);
      ch = charm_u x N x 100 x spot / 8760 / 1e9  (r = q = 0: call and put charm are equal).
      Positive = the dealer book gets LONGER delta each hour (their hedge is to sell that much).
  (The raw rows also carry the vendor's own per-contract "charm"; it is not used.)
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
# 2026-11-09 follow-ups: premium is no longer written to the DAY file (no page reads it; the
# live file keeps it) - room for vanna + charm on all three bases under the ~8 MB limit.
DAY_PREM = False
# per-minute snapshot greeks; 2026-11-09: vanna + charm on all three bases ("BASES" above)
SNAP = ("g", "gv", "gt", "va", "vav", "vat", "ch", "chv", "cht")
LIVE_MAX_AGE_S = 150            # a pull older than this (catch-up after a late start) does not write live files
WANT = (b'"idx"', b'"vix"')     # cheap pre-filter; feed() checks the pull name
try:
    import vix_map                                  # VIX tab map (2026-11-09); the Orbs feed runs without it
except Exception:                                   # pragma: no cover
    vix_map = None
try:
    import surface_feed as SF                       # Surface tab (2026-11-10); the Orbs feed runs without it
except Exception:                                   # pragma: no cover
    SF = None


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


def charm_u(spot: float, k: float, t: float, sigma: float | None) -> float:
    """Black-Scholes charm, r = q = 0: d(delta)/dt as calendar time passes, per YEAR.
    Same for calls and puts when r = q = 0:  charm = phi(d1) d2 / (2 T)  (= -d(delta)/dT;
    checked against a finite difference in time in tests/cvxmap/test_wide_feed.py)."""
    if not (sigma and sigma > 0 and t > 0 and k > 0 and spot > 0):
        return 0.0
    sq = sigma * math.sqrt(t)
    d1 = (math.log(spot / k) + 0.5 * sigma * sigma * t) / sq
    d2 = d1 - sq
    return math.exp(-d1 * d1 / 2.0) / L.SQ2PI * d2 / (2.0 * t)


def bs_delta0(spot: float, k: float, t: float, sigma: float, kind: str) -> float:
    """Black-Scholes delta, r = q = 0 (only used by the charm test)."""
    sq = sigma * math.sqrt(t)
    d1 = (math.log(spot / k) + 0.5 * sigma * sigma * t) / sq
    nd1 = 0.5 * math.erfc(-d1 / math.sqrt(2.0))
    return nd1 if kind == "C" else nd1 - 1.0


def strike_iv(k: float, fwd: float, c: dict, p: dict, fallback: float) -> float:
    """The strike's own IV: out-of-the-money side vs the forward, the other side if that one is
    missing, else the expiry's flat IV. Values outside 1%..500% count as missing."""
    def ok(x: object) -> float | None:
        try:
            x = float(x)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        return x if 0.01 < x < 5.0 else None
    a, b = (c, p) if k >= fwd else (p, c)
    return ok(a.get("volatility")) or ok(b.get("volatility")) or fallback


def expiry_books(sym, d, today, now):
    """[(group, dte, T_greeks, sigma, book, expiry, forward, T_calendar)], every expiry <= MAX_DTE.
    group '0' = today's PM expiry, '7' = the rest. T_calendar (charm) = real minutes to the
    16:15 settlement plus whole days, / 525600."""
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
        t_cal = (mins + 1440.0 * dte) / 525600.0
        out.append((grp, dte, T, sigma, bk, ed, F or spot, t_cal))
    return out


FIELDS = (*SNAP, "c", "p", "cb", "cs", "pb", "ps", "cP", "pP")


def build_slot(sym, d, today, now):
    """One block for one symbol at one minute: per strike, 0DTE and 1-7 DTE kept apart."""
    spot = d["spot"]
    ks = strikes(sym, spot)
    acc = {g: {f: [0.0] * len(ks) for f in FIELDS} for g in ("0", "7")}
    seen = {"0": [], "7": []}
    gk = 100.0 * spot * spot * 0.01 / L.B          # gamma: $bn per 1% move
    vk = 100.0 * spot * 0.01 / L.B                 # vanna: $bn of delta per 1 vol point
    hk = 100.0 * spot / L.B / 8760.0               # charm: $bn of delta per calendar HOUR
    for grp, dte, t_g, sigma, bk, ed, fwd, t_cal in expiry_books(sym, d, today, now):
        seen[grp].append({"e": ed, "dte": dte, "iv": _r(sigma, 4)})
        A = acc[grp]
        for i, k in enumerate(ks):
            cp = bk.get(k)
            if not cp:
                continue
            c, p = cp
            gu, vu = greeks_u(spot, k, t_g, sigma)
            hu = charm_u(spot, k, t_cal, strike_iv(k, fwd, c, p, sigma))
            cOI, pOI = L._n(c.get("oi")), L._n(p.get("oi"))
            cV, pV = L._n(c.get("volm")), L._n(p.get("volm"))
            cb, cs = L._n(c.get("volm_buy")), L._n(c.get("volm_sell"))
            pb, ps = L._n(p.get("volm_buy")), L._n(p.get("volm_sell"))
            # dealer books: OI, volume, signed trades (customer buy -> dealer short)
            n_oi, n_vol, n_sig = cOI - pOI, cV - pV, (cs - cb) + (ps - pb)
            for f, u, kk in (("g", gu, gk), ("va", vu, vk), ("ch", hu, hk)):
                A[f][i] += u * n_oi * kk
                A[f + "v"][i] += u * n_vol * kk
                A[f + "t"][i] += u * n_sig * kk
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
            dp = 6 if f in SNAP else (3 if f in ("cP", "pP") else 0)
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
SQ_MAX = 999                    # 2026-11-09: ... or max|value| / 999 when coarser (snap_scale)


def snap_scale(vals: list) -> float:
    """Unit of one stored snapshot array. 2026-11-09 follow-ups: nine greek arrays per group made
    the day file ~45% bigger, so each array is stored in units of max(|value|) / 999 (three digits)
    whenever that is coarser than $10k; error <= half a unit = 0.05% of the array's largest value.
    Rounded to 4 significant digits, so the stored unit is exactly the one used to encode."""
    m = max((abs(v or 0) for v in vals), default=0.0)
    return GQ if m <= GQ * SQ_MAX else float(f"{m / SQ_MAX:.4g}")


def encode_slots(slots):
    """Full blocks -> day-file v2 blocks: strikes as k0 + n (step = the symbol's), greeks as whole
    $10k (or, per array, in units of "sc"[field] $bn when coarser - snap_scale), cumulative fields
    as per-strike changes since the previous slot (premium in whole $k)."""
    out, prev = [], {}
    for j, blk in enumerate(slots):
        ks = blk["k"]
        e = {"t": blk["t"], "x": blk["x"], "k0": ks[0], "n": len(ks), "dz": 1}
        if j == len(slots) - 1 and "ex" in blk:
            e["ex"] = blk["ex"]
        sc = {}
        for g in ("0", "7"):
            for f in SNAP:
                q = snap_scale(blk[f + g])
                if q != GQ:
                    sc[f + g] = q
                e[f + g] = [round((v or 0) / q) for v in blk[f + g]]
            for f in CUM_INT + (CUM_PREM if DAY_PREM else ()):
                cur = blk[f + g]
                if f in CUM_PREM:
                    cur = [int(round((v or 0) * 1000)) for v in cur]
                pm = prev.get(f + g, {})
                e[f + g] = [int(v) - pm.get(k, 0) for k, v in zip(ks, cur, strict=False)]
                prev[f + g] = dict(zip(ks, (int(v) for v in cur), strict=False))
        if sc:
            e["sc"] = sc
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
        blk = {k: v for k, v in e.items() if k not in ("dz", "k0", "n", "sc")}
        blk["k"] = ks
        sc = e.get("sc") or {}
        for g in ("0", "7"):
            for f in SNAP:
                q = sc.get(f + g, GQ)
                blk[f + g] = [v * q for v in e.get(f + g, [])]
            for f in CUM_INT + CUM_PREM:
                if f + g not in e:          # premium: not in day files written since 2026-11-09
                    continue
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
        self.beta = {s: SF.BetaTracker() for s in SF.SURFACE_SYMS} if SF is not None else {}
        self.surf = {}

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
            if s in self.beta:
                try:   # 2026-11-10 Surface tab: every expiry kept apart; a failure never stops the Orbs feed
                    self.surf[s] = SF.build_surface(sys.modules[__name__], L, s, d, self.today, now, self.beta[s])
                except Exception as exc:
                    log("surface %s %s: %s" % (s, t, str(exc)[:120]))
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
                if s in self.surf:
                    L.atomic_write(os.path.join(DATA, "surface_live_%s.json" % s), self.surf[s])
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
                "enc": "dz1: c p cb cs pb ps as per-strike change since the previous slot; "
                       "greeks in $10k units or sc[field] $bn; premium not stored (live file only)",
                "width": {"SPX": "+-%d pts" % WIDE_PTS_SPX, "other": "+-%.1f%%" % (WIDE_FRAC * 100)},
                "syms": syms}

    def write(self):
        if self.dirty and any(self.slots.values()):
            self.dirty = False
            L.atomic_write(os.path.join(DATA, "wide_%s.json" % self.ymd), self.payload())
            write_index()
        if self.vix is not None and self.vix.changed:
            vpath = os.path.join(DATA, f"vixmap_{self.ymd}.json")
            new = not os.path.exists(vpath)
            L.atomic_write(vpath, self.vix.payload())
            self.vix.changed = False
            if new:
                write_index()          # the VIX map's day list (vix_days) for the page's fallback


def write_index():
    fs = os.listdir(DATA)
    def ymds(pre: str) -> list[str]:
        n = len(pre)
        return sorted((f[n:n + 8] for f in fs if f.startswith(pre + "2") and f.endswith(".json")),
                      reverse=True)
    days = ymds("wide_")
    vix_days = ymds("vixmap_")   # 2026-11-09: the VIX map falls back to the newest stored day
    L.atomic_write(os.path.join(DATA, "wide_index.json"),
                   {"days": days, "vix_days": vix_days,
                    "gen": L.now_et().isoformat(timespec="seconds")})


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
