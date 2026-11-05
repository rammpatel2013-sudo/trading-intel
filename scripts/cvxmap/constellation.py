"""S2 Flow Constellation feed (2026-11-04) -> data/const_<SYM>_<YYYYMMDD>.json.

What traded where today: per contract (expiry x strike x call/put) the premium bought
and sold, the customer net delta-$, and 1-minute lines of
  * cumulative dealer gamma $ (per 1% move) and vanna $ (per 1 vol pt) added by today's trades
  * cumulative bullish / bearish / net premium, all and split weeklies vs monthlies.
Bullish $ = calls bought + puts sold. Bearish $ = puts bought + calls sold.

Source = the collector's per-minute chain counters (vendor buy/sell split, day-cumulative):
value_buy/value_sell ($ premium), deltas_buy/deltas_sell (contracts x delta),
gammas_buy/gammas_sell (contracts x gamma), volm_buy/volm_sell, vanna. Measured
2026-11-04 on the 11/04 raw file: value_buy ~= contracts x mid x 100, deltas_buy ~=
contracts x delta, gammas_buy ~= contracts x gamma, so the units below are plain $.
Zero Convex calls. Main pull = 12 nearest expiries every minute (SPX/SPY/QQQ, VIX 10);
the ext pull adds expiries 12-25 every 5 minutes (SPX/SPY/QQQ, near the money).

Symbols are a parameter: any symbol the collector pulls can be added to SYMS later
(the plan is to add watchlist stocks once they are collected).

Called from cvx_live.py behind try/except: a failure here never touches live.json
or the Map. Past days:  python scripts/cvxmap/constellation.py --day 20261103  (or --all)
"""
import datetime
import glob
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import extraw  # noqa: E402

SYMS = ("SPX", "SPY", "QQQ", "VIX")
T0, T1 = "09:30", "16:15"
_days = {}          # (day, sym) -> state


def _f(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else 0.0
    except Exception:
        return 0.0


def _moved(old, new, store):
    """Emit a frame update only when the contract's premium moved by >= $2,000 and >= 2%
    since its last stored update (keeps the file ~1/2 the size). The live 'now' frame
    (store=False) is always exact."""
    if old is None:
        return True
    if not store:
        return old != new
    a, b = old[0] + old[1], new[0] + new[1]
    return abs(b - a) >= max(20, 0.02 * max(a, 1))


class _S:
    def __init__(self, sym, day):
        self.sym, self.day = sym, day
        self.exps, self.ei = [], {}
        self.ks, self.ci = [], {}
        self.cur, self.last = {}, {}
        self.frames, self.fspot, self.upd = [], [], []
        self.L = {k: [] for k in ("t", "s", "g", "v", "bull", "bear", "wb", "wr", "mb", "mr")}
        self.tot_main = None
        self.tot_ext = None
        self.ext_exps = set()
        self.main_exps = set()
        self.last_t = ""
        self.spot = 0.0

    def _cidx(self, iso, k, cp, code):
        key = (iso, k, cp)
        i = self.ci.get(key)
        if i is None:
            if iso not in self.ei:
                self.ei[iso] = len(self.exps)
                self.exps.append([iso, 1 if extraw.is_monthly(code, iso) else 0])
            i = self.ci[key] = len(self.ks)
            self.ks.append([self.ei[iso], k, cp])
        return i

    def ingest(self, entry, exp_date, ext=False):
        S = _f(entry.get("spot"))
        if S <= 0:
            return None
        tot = {"g": 0.0, "v": 0.0, "bull": 0.0, "bear": 0.0, "wb": 0.0, "wr": 0.0, "mb": 0.0, "mr": 0.0}
        seen = set()
        for head, book in entry.get("exps") or []:
            iso = exp_date(head)
            if ext and iso in self.main_exps:
                continue
            seen.add(iso)
            for k, (c, p) in book.items():
                for cp, d in (("C", c), ("P", p)):
                    vb, vs = _f(d.get("value_buy")), _f(d.get("value_sell"))
                    code = d.get("_sym") or ""
                    gn = _f(d.get("gammas_buy")) - _f(d.get("gammas_sell"))
                    vn = _f(d.get("volm_buy")) - _f(d.get("volm_sell"))
                    tot["g"] -= gn * 100.0 * S * S * 0.01
                    tot["v"] -= vn * _f(d.get("vanna")) * 100.0 * S * 0.01
                    bull, bear = (vb, vs) if cp == "C" else (vs, vb)
                    tot["bull"] += bull
                    tot["bear"] += bear
                    mon = extraw.is_monthly(code, iso)
                    tot["mb" if mon else "wb"] += bull
                    tot["mr" if mon else "wr"] += bear
                    if vb + vs <= 0:
                        continue
                    nd = (_f(d.get("deltas_buy")) - _f(d.get("deltas_sell"))) * 100.0 * S
                    i = self._cidx(iso, float(k), cp, code)
                    # compact units: premium in $100s, delta-$ in $1,000s (page multiplies back)
                    self.cur[i] = (round(vb / 100.0), round(vs / 100.0), round(nd / 1000.0), round(_f(d.get("delta")), 2))
        if ext:
            self.ext_exps = seen
            self.tot_ext = tot
        else:
            self.main_exps = seen
            self.tot_main = tot
            self.spot = S
        return tot

    def minute(self, t):
        if self.tot_main is None:
            return
        tm, te = self.tot_main, self.tot_ext or {}
        L = self.L
        L["t"].append(t)
        L["s"].append(round(self.spot, 3))
        for k in ("g", "v", "bull", "bear", "wb", "wr", "mb", "mr"):
            L[k].append(round(tm[k] + te.get(k, 0.0)))
        self.last_t = t
        if int(t[3:5]) % 5 == 0:
            self._frame(t)

    def _frame(self, t, store=True):
        f = len(self.frames)
        changed = [[f, i] + list(v) for i, v in self.cur.items() if _moved(self.last.get(i), v, store)]
        if not store:
            return changed
        self.frames.append(t)
        self.fspot.append(round(self.spot, 3))
        self.upd.extend(changed)
        for u in changed:
            self.last[u[1]] = tuple(u[2:])
        return changed

    def dump(self):
        frames, fspot, upd, live = list(self.frames), list(self.fspot), self.upd, False
        if self.last_t and (not frames or frames[-1] != self.last_t):
            extra = self._frame(self.last_t, store=False)
            frames.append(self.last_t)
            fspot.append(round(self.spot, 3))
            upd = upd + extra
            live = True
        return {"sym": self.sym, "date": self.day, "asof": datetime.datetime.now().isoformat(timespec="seconds"),
                "spot": round(self.spot, 3), "exps": self.exps, "ks": self.ks, "frames": frames, "fspot": fspot,
                "live_frame": live, "units": {"prem": 100, "delta": 1000}, "upd": upd, "lines": self.L, "ext_exps": sorted(self.ext_exps)}

    @classmethod
    def load(cls, sym, day, path):
        s = cls(sym, day)
        try:
            j = json.load(open(path, encoding="utf-8"))
        except Exception:
            return s
        s.exps = j.get("exps") or []
        s.ei = {e[0]: i for i, e in enumerate(s.exps)}
        s.ks = j.get("ks") or []
        s.ci = {(s.exps[e][0], float(k), cp): i for i, (e, k, cp) in enumerate(s.ks)}
        fr, fs, up = j.get("frames") or [], j.get("fspot") or [], j.get("upd") or []
        if j.get("live_frame") and fr:
            n = len(fr) - 1
            fr, fs, up = fr[:n], fs[:n], [u for u in up if u[0] < n]
        s.frames, s.fspot, s.upd = fr, fs, up
        for u in up:
            s.last[u[1]] = tuple(u[2:])
        s.cur = dict(s.last)
        s.L = j.get("lines") or s.L
        if s.L.get("t"):
            s.last_t = s.L["t"][-1]
            s.spot = s.L["s"][-1]
        return s


def _state(data_dir, day, sym):
    key = (day, sym)
    if key not in _days:
        for k in [k for k in _days if k[0] != day]:
            _days.pop(k, None)
        _days[key] = _S.load(sym, day, os.path.join(data_dir, "const_%s_%s.json" % (sym, day)))
    return _days[key]


def on_rec(dec, ts, data_dir, exp_date):
    """One decoded idx or vix record from cvx_live."""
    t = str(ts)[11:16]
    if not (T0 <= t <= T1):
        return
    day = str(ts)[:10].replace("-", "")
    for sym in SYMS:
        if sym not in dec:
            continue
        s = _state(data_dir, day, sym)
        if t <= s.last_t:
            continue
        if s.ingest(dec[sym], exp_date) is not None:
            s.minute(t)


def _ext(data_dir, day, decode, exp_date):
    spath = os.path.join(data_dir, "const_state_%s.json" % day)
    try:
        stt = json.load(open(spath, encoding="utf-8"))
    except Exception:
        stt = {}
    recs, off = extraw.read_new(os.path.join(data_dir, "ext_raw_%s.ndjson.gz" % day), int(stt.get("ext_off", 0)))
    if off == stt.get("ext_off"):
        return
    for rec in recs:
        t = str(rec.get("ts", ""))[11:16]
        if not (T0 <= t <= T1):
            continue
        try:
            dec = decode(rec)
        except Exception:
            continue
        for sym in SYMS:
            if sym in dec:
                _state(data_dir, day, sym).ingest(dec[sym], exp_date, ext=True)
    stt["ext_off"] = off
    extraw.atomic_write(spath, stt)


def write_index(data_dir):
    idx = {}
    for p in glob.glob(os.path.join(data_dir, "const_*_2*.json")):
        b = os.path.basename(p)[6:-5]
        sym, _, d = b.rpartition("_")
        if sym and len(d) == 8:
            idx.setdefault(sym, []).append(d)
    for v in idx.values():
        v.sort(reverse=True)
    extraw.atomic_write(os.path.join(data_dir, "const_index.json"), {"syms": idx})


def finalize(data_dir, today, decode, exp_date):
    day = today.strftime("%Y%m%d")
    try:
        _ext(data_dir, day, decode, exp_date)
    except Exception:
        pass
    for (d, sym), s in list(_days.items()):
        if d == day and s.L["t"]:
            extraw.atomic_write(os.path.join(data_dir, "const_%s_%s.json" % (sym, day)), s.dump())
    write_index(data_dir)


def backfill(day, data_dir=None):
    import gzip
    import cvx_live
    data_dir = data_dir or cvx_live.DATA
    raw = os.path.join(data_dir, "raw_%s.ndjson.gz" % day)
    if not os.path.exists(raw):
        print("no raw file", raw)
        return 0
    for sym in SYMS:
        p = os.path.join(data_dir, "const_%s_%s.json" % (sym, day))
        if os.path.exists(p):
            os.remove(p)
        _days.pop((day, sym), None)
    sp = os.path.join(data_dir, "const_state_%s.json" % day)
    if os.path.exists(sp):
        os.remove(sp)
    ext = os.path.join(data_dir, "ext_raw_%s.ndjson.gz" % day)
    ext_recs = extraw.read_new(ext, 0)[0] if os.path.exists(ext) else []
    ei, n = 0, 0
    with gzip.open(raw, "rt", encoding="utf-8") as fh:
        for ln in fh:
            try:
                rec = json.loads(ln)
            except Exception:
                continue
            if rec.get("pull") not in ("idx", "vix"):
                continue
            ts = rec.get("ts", "")
            while ei < len(ext_recs) and str(ext_recs[ei].get("ts", "")) <= ts:   # replay ext in time order
                try:
                    dx = cvx_live.decode(ext_recs[ei])
                    for sym in SYMS:
                        if sym in dx and str(ext_recs[ei].get("ts", ""))[11:16] >= T0:
                            _state(data_dir, day, sym).ingest(dx[sym], cvx_live.exp_date, ext=True)
                except Exception:
                    pass
                ei += 1
            try:
                on_rec(cvx_live.decode(rec), ts, data_dir, cvx_live.exp_date)
                n += 1
            except Exception:
                pass
    for (d, sym), s in list(_days.items()):
        if d == day and s.L["t"]:
            s.last_t = s.L["t"][-1]
            extraw.atomic_write(os.path.join(data_dir, "const_%s_%s.json" % (sym, day)), s.dump())
    if os.path.exists(ext):
        extraw.atomic_write(sp, {"ext_off": os.path.getsize(ext)})
    write_index(data_dir)
    print("constellation backfill %s: %d records" % (day, n))
    return n


if __name__ == "__main__":
    a = sys.argv[1:]
    if "--day" in a:
        backfill(a[a.index("--day") + 1])
    elif "--all" in a:
        import cvx_live
        for p in sorted(glob.glob(os.path.join(cvx_live.DATA, "raw_2*.ndjson.gz"))):
            d = os.path.basename(p)[4:12]
            if not os.path.exists(os.path.join(cvx_live.DATA, "const_SPX_%s.json" % d)):
                backfill(d)
