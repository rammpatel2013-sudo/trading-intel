"""
Vol-regime data for the TEST cockpit VIX tab (created 2026-11-08). Runs inside serve_cvx_test.py (port 8898) only.

Free Cboe data, the same CDN vixdash.py already uses - NOT Convex, zero API budget, no production file touched:
  quotes  cdn-api.cboe.com delayed_quotes  (_VIX _VIX9D _VIXEQ _DSPX _COR1M _COR3M _SPX _VXN, ETFs VIXM VIXY), ~15 min delayed
  history cdn.cboe.com daily_prices CSV (VIX, VIX9D, SPX) + delayed_quotes/charts/historical (VIXM, VIXY)

Writes (only under scripts/cvxmap/data/test/):
  volx.json             latest snapshot the page reads: quotes, intraday series, the Doublewide gauge
  volx_YYYYMMDD.json    the day's 1-minute intraday series (so a restart keeps the morning)

VX1 Doublewide regime gauge (VIXM/VIXY ratio, Substack 2026-11-07; claude/vol-regime-knowledge-20261108.md):
  ratio = VIXM / VIXY daily close; 8-day vs 21-day EMA (span, adjust=False);
  filters: VIX9D/VIX > 1.00 and SPX < its 21-day EMA.
  CALM (8>21, neither filter) / UNCONFIRMED (8<21, neither) / MIXED (8<21, one) / CONFIRMED (8<21, both).
  8>21 with a filter met = CALM* (his table has no row for it; shown as calm with a warning).
  Today's row is PROVISIONAL (live quotes) until the cash close; he judges on 15:00-16:00 closes.
VX2 variance stack: VIX^2 + DSPX^2 ~= VIXEQ^2; transmission share = VIX^2 / (VIX^2 + DSPX^2).
"""
import datetime, json, os, threading, time

_Q_IDX = "https://cdn-api.cboe.com/api/global/delayed_quotes/quotes/_%s.json"
_Q_ETF = "https://cdn-api.cboe.com/api/global/delayed_quotes/quotes/%s.json"
_H_IDX = "https://cdn.cboe.com/api/global/us_indices/daily_prices/%s_History.csv"
_H_ETF = "https://cdn.cboe.com/api/global/delayed_quotes/charts/historical/%s.json"
_UA = {"User-Agent": "Mozilla/5.0"}
IDX = ("VIX", "VIX9D", "VIXEQ", "DSPX", "COR1M", "COR3M", "SPX", "VXN")
ETF = ("VIXM", "VIXY")
INTRA = ("VIX", "VIX9D", "VIXEQ", "DSPX", "COR1M", "COR3M", "SPX")
H_IDX = ("VIX", "VIX9D", "SPX")
RTH0, RTH1 = 9 * 60 + 30, 16 * 60 + 15


def _f(x):
    try:
        return float(x)
    except Exception:
        return 0.0


# ---------------------------------------------------------------- pure maths (tested without the network)
def ema(vals, span):
    a, out, e = 2.0 / (span + 1.0), [], None
    for v in vals:
        e = v if e is None else e + a * (v - e)
        out.append(e)
    return out


def label(up, f_v9, f_spx):
    if up:
        return "CALM" if not (f_v9 or f_spx) else "CALM*"
    n = int(bool(f_v9)) + int(bool(f_spx))
    return ("UNCONFIRMED", "MIXED", "CONFIRMED")[n]


def qday(x, today):
    """Session date of a quote from its last_trade_time ('2026-11-06T16:15:01'); today if missing."""
    try:
        return datetime.date.fromisoformat(str((x or {}).get("t") or "")[:10])
    except Exception:
        return today


def gauge(hist, q, today, final=False):
    """hist: {sym: [(date, close), ...]} daily closes; q: {sym: {"v": last, "t": last trade time}} live quotes.
    Each live quote is added at ITS OWN session date where the history doesn't have that date yet (ETF history lags
    a day; on a weekend the quotes are Friday's). Rows = every date where all five series exist. The newest row is
    provisional while it is today's session and before the 16:15 close (unless final). EMAs run over the joined history."""
    need = ("VIXM", "VIXY", "VIX9D", "VIX", "SPX")
    maps = {s: dict(hist.get(s, [])) for s in need}
    for s in need:
        x = q.get(s) or {}
        v = _f(x.get("v"))
        if v > 0:
            d = qday(x, today)
            if d <= today and d not in maps[s]:
                maps[s][d] = v
    if not all(maps.values()):
        return None
    days = sorted(set.intersection(*[set(m) for m in maps.values()]))
    rows = [{"d": d, "vixm": maps["VIXM"][d], "vixy": maps["VIXY"][d], "v9": maps["VIX9D"][d], "vix": maps["VIX"][d],
             "spx": maps["SPX"][d], "prov": (d == today and not final)} for d in days]
    if len(rows) < 25:
        return None
    r = [x["vixm"] / x["vixy"] for x in rows]
    e8, e21 = ema(r, 8), ema(r, 21)
    s8, s21 = ema([x["spx"] for x in rows], 8), ema([x["spx"] for x in rows], 21)
    out = []
    for i, x in enumerate(rows):
        v9r = x["v9"] / x["vix"]
        up = e8[i] > e21[i]
        fv, fs = v9r > 1.0, x["spx"] < s21[i]
        out.append({"d": x["d"].isoformat(), "r": round(r[i], 5), "e8": round(e8[i], 5), "e21": round(e21[i], 5),
                    "v9r": round(v9r, 4), "spx": round(x["spx"], 2), "s8": round(s8[i], 2), "s21": round(s21[i], 2),
                    "f_v9": fv, "f_spx": fs, "lab": label(up, fv, fs), "prov": x["prov"]})
    return out


def cues(rows, v9r_intra_max=None):
    """Doublewide exit / re-entry cues from VIX9D/VIX (daily closes, plus today's intraday high when given).
    close_shorts: VIX9D/VIX peaked above 1.15 in the last 5 sessions and is now at or below 1.05.
    reentry:      peaked above 1.15 in the last 10 sessions, now below 1.00, and SPX above its 8-day EMA."""
    if not rows:
        return {}
    last = rows[-1]
    hi5 = max([x["v9r"] for x in rows[-5:]] + ([v9r_intra_max] if v9r_intra_max else []))
    hi10 = max([x["v9r"] for x in rows[-10:]] + ([v9r_intra_max] if v9r_intra_max else []))
    return {"peak5": round(hi5, 4), "peak10": round(hi10, 4),
            "close_shorts": hi5 > 1.15 and last["v9r"] <= 1.05,
            "reentry": hi10 > 1.15 and last["v9r"] < 1.0 and last["spx"] > last["s8"],
            "reentry_need": {"v9r_below_1": last["v9r"] < 1.0, "spx_above_8d": last["spx"] > last["s8"], "s8": last["s8"]}}


def stack(v):
    """Variance stack for one time point: v = {VIX, DSPX, VIXEQ}. Share = VIX^2/(VIX^2+DSPX^2)."""
    a, b, c = _f(v.get("VIX")), _f(v.get("DSPX")), _f(v.get("VIXEQ"))
    if not (a > 0 and b > 0):
        return None
    tot = a * a + b * b
    return {"vix2": round(a * a, 1), "dspx2": round(b * b, 1), "tot": round(tot, 1), "sqrt": round(tot ** 0.5, 3),
            "share": round(a * a / tot, 4), "gap": round(tot ** 0.5 - c, 3) if c > 0 else None}


# ---------------------------------------------------------------- network + loop
class VolX:
    def __init__(self, out_dir, clock):
        self.out, self.clock = out_dir, clock
        self.hist, self.hist_day, self.hist_try = {}, None, 0.0
        self.q, self.day, self.intra = {}, None, {"t": []}
        self.err = None

    def _get(self, url, timeout=8):
        import requests
        r = requests.get(url, timeout=timeout, headers=_UA)
        r.raise_for_status()
        return r

    def quotes(self):
        q = {}
        for s in IDX + ETF:
            try:
                d = (self._get((_Q_IDX if s in IDX else _Q_ETF) % s).json() or {}).get("data") or {}
                v = _f(d.get("current_price") or d.get("last_trade_price"))
                if v > 0:
                    q[s] = {"v": v, "prev": _f(d.get("prev_day_close")), "t": d.get("last_trade_time") or ""}
            except Exception:
                pass
        return q

    def history(self, today):
        h = {}
        for s in H_IDX:
            try:
                rows = []
                for line in self._get(_H_IDX % s, timeout=15).text.splitlines()[1:]:
                    p = line.strip().split(",")
                    if len(p) < 2:
                        continue
                    m, d, y = p[0].split("/")
                    dt, c = datetime.date(int(y), int(m), int(d)), _f(p[-1])
                    if dt < today and c > 0:
                        rows.append((dt, c))
                h[s] = rows[-300:]
            except Exception:
                pass
        for s in ETF:
            try:
                js = self._get(_H_ETF % s, timeout=15).json() or {}
                rows = []
                for x in js.get("data") or []:
                    dt = datetime.date.fromisoformat(str(x.get("date"))[:10])
                    c = _f(x.get("close"))
                    if dt < today and c > 0:
                        rows.append((dt, c))
                h[s] = rows[-300:]
            except Exception:
                pass
        return h

    def _day_file(self, day):
        return os.path.join(self.out, "volx_%s.json" % day.strftime("%Y%m%d"))

    def _load_day(self, day):
        self.day, self.intra = day, {"t": []}
        for s in INTRA:
            self.intra[s] = []
        try:
            j = json.load(open(self._day_file(day)))
            if isinstance(j, dict) and isinstance(j.get("t"), list):
                self.intra = j
                for s in INTRA:
                    self.intra.setdefault(s, [None] * len(j["t"]))
        except Exception:
            pass

    def step(self):
        now = self.clock()
        today = now.date()
        if self.day != today:
            self._load_day(today)
        if self.hist_day != today and time.time() - self.hist_try > 900:
            self.hist_try = time.time()
            h = self.history(today)
            if len(h) >= 4:
                self.hist, self.hist_day = h, today
        q = self.quotes()
        if q:
            self.q = q
        m = now.hour * 60 + now.minute
        if q and RTH0 <= m <= RTH1 and now.weekday() < 5:
            t = now.strftime("%H:%M")
            if not self.intra["t"] or self.intra["t"][-1] != t:
                self.intra["t"].append(t)
                for s in INTRA:
                    self.intra[s].append((q.get(s) or {}).get("v"))
                tmp = self._day_file(today) + ".tmp"
                with open(tmp, "w") as fh:
                    json.dump(self.intra, fh, separators=(",", ":"))
                os.replace(tmp, self._day_file(today))
        self.write(now)

    def write(self, now):
        today = now.date()
        m = now.hour * 60 + now.minute
        final = m >= 16 * 60 + 15 or now.weekday() >= 5
        g = gauge(self.hist, self.q, today, final=final)
        v9 = [a / b for a, b in zip(self.intra.get("VIX9D", []), self.intra.get("VIX", [])) if a and b]
        snap = {"asof": now.strftime("%Y-%m-%dT%H:%M:%S"), "src": "Cboe delayed quotes (~15 min) + Cboe daily history; not Convex",
                "q": self.q, "intra": self.intra, "gauge": g[-60:] if g else None,
                "cues": cues(g, max(v9) if v9 else None) if g else None,
                "stack_now": stack({s: (self.q.get(s) or {}).get("v") for s in ("VIX", "DSPX", "VIXEQ")}),
                "stack_prev": stack({s: (self.q.get(s) or {}).get("prev") for s in ("VIX", "DSPX", "VIXEQ")}),
                "hist_through": {s: (v[-1][0].isoformat() if v else None) for s, v in self.hist.items()}}
        os.makedirs(self.out, exist_ok=True)
        tmp = os.path.join(self.out, "volx.json.tmp")
        with open(tmp, "w") as fh:
            json.dump(snap, fh, separators=(",", ":"))
        os.replace(tmp, os.path.join(self.out, "volx.json"))

    def run(self):
        while True:
            try:
                self.step()
                self.err = None
            except Exception as e:
                self.err = str(e)[:200]
                print("volx error:", self.err, flush=True)
            time.sleep(60)


def start(out_dir, clock):
    v = VolX(out_dir, clock)
    threading.Thread(target=v.run, name="volx", daemon=True).start()
    return v
