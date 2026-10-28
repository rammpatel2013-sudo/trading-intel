"""
VIX tab data for the Convex cockpit - writer side. Created 2026-10-28.

Everything here is ADDITIVE to live.json and never touches the SPX/SPY/QQQ map:
  VIX.exps[i]   settled and empty expiries dropped; each expiry gains
                F (put-call-parity forward), K0, atmK/atmIV (at the forward,
                not at VIX spot), iv{} (model-free implied variance, Cboe VIX
                formula on that expiry - Dr.Vega's method), contrib (share of
                the variance by strike) and sq{} (customer call contracts
                bought/sold above the forward = the VIX gamma-squeeze read).
  VIXR          regime board: Cboe delayed index quotes + 1-year daily history
                -> percentile, colour state, 1d/1w/1m change, 20-day sparkline,
                term curves today / yesterday / 1 week / 1 month.
  EVT           event calendar from events.json, with days away.

Network work (Cboe CDN, free, NOT Convex - zero API budget) runs in a daemon
thread. The writer only ever reads the last finished snapshot, so a slow or
dead Cboe can never stall live.json.
"""
import os, json, math, time, datetime, threading

HERE = os.path.dirname(os.path.abspath(__file__))
R_RATE = 0.04
SOQ_MIN = 9 * 60 + 30          # VIX options settle on the Wednesday-open SOQ
_Q_URL = "https://cdn-api.cboe.com/api/global/delayed_quotes/quotes/_%s.json"
_H_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/%s_History.csv"
_UA = {"User-Agent": "Mozilla/5.0"}

QUOTE_SYMS = ("VIX1D", "VIX9D", "VIX", "VIX3M", "VIX6M", "VIX1Y", "VVIX", "COR1M", "SPX")
TENORS = (("VIX1D", 1), ("VIX9D", 9), ("VIX", 30), ("VIX3M", 93), ("VIX6M", 186), ("VIX1Y", 365))


def _f(x):
    try:
        return float(x)
    except Exception:
        return 0.0


# ======================================================================
#  Per-expiry VIX chain maths
# ======================================================================
def _mid(d):
    """Bid/ask mid; falls back to the last trade only when there is no quote."""
    b, a = d.get("bid_price"), d.get("ask_price")
    if b is not None and a is not None and _f(a) > 0:
        return (_f(b) + _f(a)) / 2.0
    p = _f(d.get("price"))
    return p if p > 0 else None


def _has_bid(d):
    b = d.get("bid_price")
    if b is None:                       # no quote fields at all: judge on the trade
        return _f(d.get("price")) > 0
    return _f(b) > 0


def forward(book):
    """Cboe method: strike with the smallest |C-P| mid gap, F = K + (C - P)."""
    best = None
    for k, (c, p) in book.items():
        mc, mp = _mid(c), _mid(p)
        if mc is None or mp is None:
            continue
        g = abs(mc - mp)
        if best is None or g < best[0]:
            best = (g, k, mc - mp)
    if best is None:
        return None
    return best[1] + best[2]


def years_to_soq(exp_iso, now):
    """Time to the expiry-morning SOQ (09:30 ET), in years. <=0 means settled."""
    d = datetime.date.fromisoformat(exp_iso)
    soq = datetime.datetime(d.year, d.month, d.day, 9, 30)
    return (soq - now).total_seconds() / 60.0 / 525600.0


def implied_variance(book, F, T, r=R_RATE):
    """Model-free variance of one expiry (Cboe VIX white paper, single term).

    sigma^2 = (2/T) sum dK/K^2 e^{rT} Q(K)  -  (1/T) (F/K0 - 1)^2
    Q = OTM mid (puts below K0, calls above, the average at K0). Walking out
    from K0 a strike with no bid is skipped and two in a row end that wing,
    exactly as Cboe does. `complete` says whether the CALL wing ended that way
    (True) or simply ran out of listed strikes in our pull (False = lower bound).
    """
    ks = sorted(book)
    below = [k for k in ks if k <= F]
    if not below or T <= 0:
        return None
    K0 = below[-1]
    sel = {}
    c0, p0 = book[K0]
    mc, mp = _mid(c0), _mid(p0)
    if mc is None and mp is None:
        return None
    sel[K0] = ((mc or 0) + (mp or 0)) / (2.0 if (mc is not None and mp is not None) else 1.0)

    def wing(strikes, side):
        zeros, ended = 0, False
        for k in strikes:
            leg = book[k][0 if side == "C" else 1]
            if not _has_bid(leg):
                zeros += 1
                if zeros >= 2:
                    ended = True
                    break
                continue
            zeros = 0
            m = _mid(leg)
            if m is not None and m > 0:
                sel[k] = m
        return ended

    wing([k for k in ks if k < K0][::-1], "P")
    call_done = wing([k for k in ks if k > K0], "C")

    kk = sorted(sel)
    if len(kk) < 3:
        return None
    er = math.exp(r * T)
    parts = []
    for i, k in enumerate(kk):
        if i == 0:
            dk = kk[1] - kk[0]
        elif i == len(kk) - 1:
            dk = kk[-1] - kk[-2]
        else:
            dk = (kk[i + 1] - kk[i - 1]) / 2.0
        parts.append((k, dk / (k * k) * er * sel[k]))
    tot = sum(v for _, v in parts)
    if tot <= 0:
        return None
    var = 2.0 / T * tot - (F / K0 - 1.0) ** 2 / T
    if var <= 0:
        return None
    vol = math.sqrt(var)
    put = sum(v for k, v in parts if k < K0) / tot
    k0s = sum(v for k, v in parts if k == K0) / tot
    call = sum(v for k, v in parts if k > K0) / tot
    s = vol * math.sqrt(T)
    return {
        "K0": K0, "T": round(T * 365.0, 3), "var": round(var, 5), "vol": round(vol, 5),
        "put": round(put, 4), "k0": round(k0s, 4), "call": round(call, 4),
        "ratio": round(call / (put + k0s), 3) if (put + k0s) > 0 else None,
        "lo": round(F * math.exp(-s), 2), "hi": round(F * math.exp(s), 2),
        "kmax": kk[-1], "complete": bool(call_done),
        "contrib": [[k, round(v / tot * 100.0, 2)] for k, v in parts],
    }


def squeeze_read(book, F):
    """Customer call contracts traded above the forward today.

    contracts = gammas_buy / gamma (checked against volume 2026-10-28: median
    ratio 0.997, n=79). Net BOUGHT above F = market makers short VIX gamma =
    the squeeze set-up. Log-only until the scorecard says what threshold matters.
    """
    b = s = 0.0
    for k, (c, _p) in book.items():
        g = _f(c.get("gamma"))
        if k <= F or g <= 0:
            continue
        b += _f(c.get("gammas_buy")) / g
        s += _f(c.get("gammas_sell")) / g
    return {"bought": round(b), "sold": round(s), "net": round(b - s), "on": (b - s) > 0}


def _vendor_iv(c, p):
    ci, pi = _f(c.get("volatility")), _f(p.get("volatility"))
    return (ci + pi) / 2.0 if ci > 0 and pi > 0 else (ci if ci > 0 else pi)


def enrich_expiry(exp_iso, book, now):
    """Extra per-expiry fields, or None when the expiry should be dropped."""
    if years_to_soq(exp_iso, now) <= 0:
        return None                                    # settled at this morning's open
    oi = sum(_f(c.get("oi")) + _f(p.get("oi")) for c, p in book.values())
    vol = sum(_f(c.get("volm")) + _f(p.get("volm")) for c, p in book.values())
    if oi <= 0 and vol <= 0:
        return None                                    # listed but empty
    out = {}
    F = forward(book)
    if F is None:
        return out
    out["F"] = round(F, 3)
    atmK = min(book, key=lambda k: abs(k - F))
    c, p = book[atmK]
    out["atmK"] = atmK
    out["atmIV"] = round(_vendor_iv(c, p), 4)
    T = years_to_soq(exp_iso, now)
    try:
        iv = implied_variance(book, F, T)
    except Exception:
        iv = None
    if iv:
        out["K0"] = iv.pop("K0")
        out["contrib"] = iv.pop("contrib")
        out["iv"] = iv
    out["sq"] = squeeze_read(book, F)
    return out


# ======================================================================
#  Regime board: Cboe quotes + history, in a background thread
# ======================================================================
class _Regime:
    def __init__(self):
        self.lock = threading.Lock()
        self.snap = None
        self.quotes = {}
        self.hist = {}            # sym -> [(date, close)], oldest first, before today
        self.hist_day = None
        self.hist_try = 0.0
        self.started = False

    # ---- network ----
    def _get(self, url, timeout=6):
        import requests
        r = requests.get(url, timeout=timeout, headers=_UA)
        r.raise_for_status()
        return r

    def _quotes(self):
        q = {}
        for s in QUOTE_SYMS:
            try:
                d = (self._get(_Q_URL % s).json() or {}).get("data") or {}
                v = _f(d.get("current_price") or d.get("last_trade_price"))
                if v > 0:
                    q[s] = {"v": v, "prev": _f(d.get("prev_day_close")),
                            "t": d.get("last_trade_time") or ""}
            except Exception:
                pass
        return q

    def _history(self, today):
        h = {}
        for s in QUOTE_SYMS:
            try:
                rows = []
                for line in self._get(_H_URL % s, timeout=15).text.splitlines()[1:]:
                    parts = line.strip().split(",")
                    if len(parts) < 2:
                        continue
                    m, d, y = parts[0].split("/")
                    dt = datetime.date(int(y), int(m), int(d))
                    c = _f(parts[-1])
                    if dt < today and c > 0:
                        rows.append((dt, c))
                h[s] = rows[-400:]
            except Exception:
                pass
        return h

    # ---- loop ----
    def start(self, clock=None):
        if self.started:
            return
        self.started = True
        self.clock = clock or datetime.datetime.now
        threading.Thread(target=self._run, name="vix-regime", daemon=True).start()

    def _run(self):
        while True:
            try:
                now = self.clock()
                today = now.date()
                if self.hist_day != today and time.time() - self.hist_try > 900:
                    self.hist_try = time.time()
                    h = self._history(today)
                    if len(h) >= 6:
                        self.hist, self.hist_day = h, today
                q = self._quotes()
                if q:
                    self.quotes = q
                snap = build_regime(self.quotes, self.hist, today)
                with self.lock:
                    self.snap = snap
            except Exception:
                pass
            time.sleep(60)

    def get(self):
        with self.lock:
            return self.snap


_REG = _Regime()


def start_regime(clock=None):
    """clock = the writer's ET clock (cvx_live.now_et); NAS containers run UTC."""
    _REG.start(clock)


def regime_snapshot():
    return _REG.get()


def quote(sym):
    s = _REG.get()
    return ((s or {}).get("idx") or {}).get(sym) or 0.0


# ---- pure maths, testable without the network ----
def _series(hist, num, den=None):
    if den is None:
        return [(d, v) for d, v in hist.get(num, [])]
    dd = dict(hist.get(den, []))
    return [(d, v / dd[d]) for d, v in hist.get(num, []) if dd.get(d)]


def _rv20(spx):
    out = []
    for i in range(20, len(spx)):
        rets = [math.log(spx[j][1] / spx[j - 1][1]) for j in range(i - 19, i + 1)]
        m = sum(rets) / 20.0
        sd = math.sqrt(sum((x - m) ** 2 for x in rets) / 19.0)
        out.append((spx[i][0], sd * math.sqrt(252.0) * 100.0))
    return out


def _on_or_before(ser, day):
    best = None
    for d, v in ser:
        if d <= day:
            best = v
        else:
            break
    return best


def _state(risk, ratio_val=None):
    if risk >= 90.0 or (ratio_val is not None and ratio_val >= 1.0):
        return "red"
    if risk >= 66.7:                  # same cut-offs as the approved v2 mockup
        return "orange"
    if risk >= 33.3:
        return "yellow"
    return "green"


def build_regime(quotes, hist, today):
    if not quotes:
        return None
    qv = {s: q["v"] for s, q in quotes.items()}
    spx = hist.get("SPX", [])
    rv = _rv20(spx)
    rv_now = None
    if len(spx) >= 20 and qv.get("SPX"):
        closes = [v for _, v in spx[-19:]] + [qv["SPX"]]
        prev = spx[-20][1]
        seq = [prev] + closes
        rets = [math.log(seq[i] / seq[i - 1]) for i in range(1, len(seq))]
        m = sum(rets) / len(rets)
        rv_now = math.sqrt(sum((x - m) ** 2 for x in rets) / (len(rets) - 1)) * math.sqrt(252.0) * 100.0
    vix_h = dict(hist.get("VIX", []))
    defs = [
        ("r9d", "VIX9D ÷ VIX", "fast", _series(hist, "VIX9D", "VIX"),
         (qv.get("VIX9D", 0) / qv["VIX"]) if qv.get("VIX") else None, True, True, 3),
        ("r3m", "VIX ÷ VIX3M", "medium", _series(hist, "VIX", "VIX3M"),
         (qv["VIX"] / qv["VIX3M"]) if qv.get("VIX") and qv.get("VIX3M") else None, True, True, 3),
        ("r6m", "VIX ÷ VIX6M", "slow", _series(hist, "VIX", "VIX6M"),
         (qv["VIX"] / qv["VIX6M"]) if qv.get("VIX") and qv.get("VIX6M") else None, True, True, 3),
        ("r1y", "VIX ÷ VIX1Y", "long", _series(hist, "VIX", "VIX1Y"),
         (qv["VIX"] / qv["VIX1Y"]) if qv.get("VIX") and qv.get("VIX1Y") else None, True, True, 3),
        ("vvix", "VVIX", "vol of VIX", _series(hist, "VVIX"), qv.get("VVIX"), True, False, 2),
        ("vix", "VIX", "30-day", _series(hist, "VIX"), qv.get("VIX"), True, False, 2),
        ("cor1m", "COR1M", "implied corr.", _series(hist, "COR1M"), qv.get("COR1M"), False, False, 2),
        ("vrp", "VRP", "VIX − SPX RV20", [(d, vix_h[d] - v) for d, v in rv if d in vix_h],
         (qv["VIX"] - rv_now) if qv.get("VIX") and rv_now else None, False, False, 2),
    ]
    rows, counts = [], {"red": 0, "orange": 0, "yellow": 0, "green": 0}
    wk, mo = today - datetime.timedelta(days=7), today - datetime.timedelta(days=30)
    for mid, name, tag, ser, now, hi, is_ratio, dp in defs:
        if now is None or len(ser) < 60:
            continue
        yr = [v for _, v in ser[-252:]]
        below = sum(1 for x in yr if x < now) + 0.5 * sum(1 for x in yr if x == now)
        pct = 100.0 * below / len(yr)
        risk = pct if hi else 100.0 - pct
        st = _state(risk, now if is_ratio else None)
        counts[st] += 1
        y1, w1, m1 = ser[-1][1], _on_or_before(ser, wk), _on_or_before(ser, mo)
        rows.append({
            "id": mid, "name": name, "tag": tag, "v": round(now, dp + 1), "dp": dp,
            "pct": round(pct, 1), "risk": round(risk, 1), "state": st, "hi": hi,
            "d1": round(now - y1, dp + 1),
            "d5": round(now - w1, dp + 1) if w1 is not None else None,
            "d21": round(now - m1, dp + 1) if m1 is not None else None,
            "spark": [round(v, dp + 1) for _, v in ser[-20:]] + [round(now, dp + 1)],
        })

    def curve(day=None):
        vals = []
        for s, _days in TENORS:
            if day is None:
                vals.append(qv.get(s))
            else:
                vals.append(_on_or_before(hist.get(s, []), day))
        return [round(v, 2) if v else None for v in vals]

    last = (hist.get("VIX") or [(None, None)])[-1][0]
    return {
        "asof": (quotes.get("VIX") or {}).get("t", ""),
        "hist_through": last.isoformat() if last else None,
        "idx": {s: round(v, 2) for s, v in qv.items()},
        "rv20": round(rv_now, 2) if rv_now else None,
        "counts": counts, "rows": rows,
        "tenors": [[s, d] for s, d in TENORS],
        "curves": {
            "today": curve(),
            "y": curve(last) if last else None,
            "w": curve(wk), "m": curve(mo),
            "dates": {"y": last.isoformat() if last else None,
                      "w": (_last_date(hist, wk) or ""), "m": (_last_date(hist, mo) or "")},
        },
    }


def _last_date(hist, day):
    best = None
    for d, _v in hist.get("VIX", []):
        if d <= day:
            best = d
    return best.isoformat() if best else None


# ======================================================================
#  Event calendar
# ======================================================================
_EVT = {"mtime": None, "rows": []}


def events(today, path=None, horizon=60):
    path = path or os.path.join(HERE, "events.json")
    try:
        mt = os.path.getmtime(path)
        if mt != _EVT["mtime"]:
            with open(path, encoding="utf-8") as fh:
                _EVT["rows"] = json.load(fh).get("events") or []
            _EVT["mtime"] = mt
    except Exception:
        return []
    out = []
    for e in _EVT["rows"]:
        try:
            d = datetime.date.fromisoformat(e["d"])
        except Exception:
            continue
        away = (d - today).days
        if 0 <= away <= horizon:
            out.append({"d": e["d"], "t": e.get("t", ""), "kind": e.get("kind", ""),
                        "label": e.get("label", ""), "away": away})
    out.sort(key=lambda x: (x["d"], x["t"]))
    return out
