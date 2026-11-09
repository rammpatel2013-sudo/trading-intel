"""
volx.py - vol-regime data for the cockpit. Created 2026-11-08 inside the TEST server; 2026-11-09
moved to its own process / container `cvxmap-volx` (scripts/nas/run_volx.sh), so the MAIN
cockpit's Density + VIX options can use it.

Free index data from the same delayed-quote CDN vixdash.py already uses - NOT the options data
vendor, zero API budget, no production file touched:
  quotes   _VIX _VIX9D _VIXEQ _DSPX _COR1M _COR3M _SPX _VXN, ETFs VIXM VIXY (~15 min delayed),
           once a minute
  history  daily closes VIX, VIX9D, SPX, VIXM, VIXY, every 15 min until today's history is in

Writes only these two files under scripts/cvxmap/data/ (served as-is by cvxmap-serve :8899 and
cvxmap-test :8898):
  volx.json            latest snapshot the page reads: quotes, intraday series, the regime gauge
  volx_YYYYMMDD.json   the day's 1-minute intraday series (so a restart keeps the morning)

VX1 regime gauge (VIXM/VIXY ratio; claude/vol-regime-knowledge-20261108.md):
  ratio = VIXM / VIXY daily close; 8-day vs 21-day EMA (span, adjust=False);
  filters: VIX9D/VIX > 1.00 and SPX < its 21-day EMA.
  CALM (8>21, neither) / UNCONFIRMED (8<21, neither) / MIXED (8<21, one) / CONFIRMED (8<21, both).
  8>21 with a filter met = CALM* (the source table has no row for it; shown as calm + warning).
  Today's row is PROVISIONAL (live quotes) until the cash close. Not tested by us as a predictor.
VX2 variance stack: VIX^2 + DSPX^2 ~= VIXEQ^2; transmission share = VIX^2 / (VIX^2 + DSPX^2).

RUN  python scripts/cvxmap/volx.py         one step a minute; exits at 16:30 ET (weekends: once)
     python scripts/cvxmap/volx.py --once  one step, then exit
NAS  sudo bash .../trading-intel/scripts/nas/run_volx.sh start | stop | status | once
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import threading
import time
from collections.abc import Callable

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
_Q_IDX = "https://cdn-api.cboe.com/api/global/delayed_quotes/quotes/_%s.json"
_Q_ETF = "https://cdn-api.cboe.com/api/global/delayed_quotes/quotes/%s.json"
_H_IDX = "https://cdn.cboe.com/api/global/us_indices/daily_prices/%s_History.csv"
_H_ETF = "https://cdn.cboe.com/api/global/delayed_quotes/charts/historical/%s.json"
_UA = {"User-Agent": "Mozilla/5.0"}
IDX = ("VIX", "VIX9D", "VIXEQ", "DSPX", "COR1M", "COR3M", "SPX", "VXN")
ETF = ("VIXM", "VIXY")
INTRA = ("VIX", "VIX9D", "VIXEQ", "DSPX", "COR1M", "COR3M", "SPX")
H_IDX = ("VIX", "VIX9D", "SPX")
STK = ("VIX", "DSPX", "VIXEQ")
RTH0, RTH1 = 9 * 60 + 30, 16 * 60 + 15
END_MIN = 16 * 60 + 30          # the loop exits after this (one final write after 16:15)
STEP_S = 60
SRC = "free delayed index quotes (~15 min) + daily history; not the options data vendor"
try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:                                   # pragma: no cover
    _ET = None


def now_et() -> datetime.datetime:
    if _ET is not None:
        return datetime.datetime.now(_ET).replace(tzinfo=None)
    return datetime.datetime.utcnow() - datetime.timedelta(hours=5)


def _f(x: object) -> float:
    try:
        return float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------- pure maths (no network)
def ema(vals: list[float], span: int) -> list[float]:
    a, out, e = 2.0 / (span + 1.0), [], None
    for v in vals:
        e = v if e is None else e + a * (v - e)
        out.append(e)
    return out


def label(up: bool, f_v9: bool, f_spx: bool) -> str:
    if up:
        return "CALM" if not (f_v9 or f_spx) else "CALM*"
    n = int(bool(f_v9)) + int(bool(f_spx))
    return ("UNCONFIRMED", "MIXED", "CONFIRMED")[n]


def qday(x: dict | None, today: datetime.date) -> datetime.date:
    """Session date of a quote from its last_trade_time ('2026-11-06T16:15:01'); else today."""
    try:
        return datetime.date.fromisoformat(str((x or {}).get("t") or "")[:10])
    except ValueError:
        return today


def gauge(hist: dict, q: dict, today: datetime.date, final: bool = False) -> list[dict] | None:
    """hist: {sym: [(date, close), ...]} daily closes; q: {sym: {"v": last, "t": last trade time}}.
    Each live quote is added at ITS OWN session date where the history doesn't have that date yet
    (ETF history lags a day; on a weekend the quotes are Friday's). Rows = every date where all five
    series exist. The newest row is provisional while it is today's session and before the 16:15
    close (unless final). EMAs run over the joined history."""
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
    rows = [{"d": d, "vixm": maps["VIXM"][d], "vixy": maps["VIXY"][d], "v9": maps["VIX9D"][d],
             "vix": maps["VIX"][d], "spx": maps["SPX"][d], "prov": (d == today and not final)}
            for d in days]
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
        out.append({"d": x["d"].isoformat(), "r": round(r[i], 5), "e8": round(e8[i], 5),
                    "e21": round(e21[i], 5), "v9r": round(v9r, 4), "spx": round(x["spx"], 2),
                    "s8": round(s8[i], 2), "s21": round(s21[i], 2), "f_v9": fv, "f_spx": fs,
                    "lab": label(up, fv, fs), "prov": x["prov"]})
    return out


def cues(rows: list[dict], v9r_intra_max: float | None = None) -> dict:
    """Exit / re-entry cues from VIX9D/VIX (daily closes, plus today's intraday high when given).
    close_shorts: VIX9D/VIX peaked above 1.15 in the last 5 sessions and is now at or below 1.05.
    reentry:      peaked above 1.15 in the last 10 sessions, now below 1.00, SPX over its 8d EMA."""
    if not rows:
        return {}
    last = rows[-1]
    extra = [v9r_intra_max] if v9r_intra_max else []
    hi5 = max([x["v9r"] for x in rows[-5:]] + extra)
    hi10 = max([x["v9r"] for x in rows[-10:]] + extra)
    return {"peak5": round(hi5, 4), "peak10": round(hi10, 4),
            "close_shorts": hi5 > 1.15 and last["v9r"] <= 1.05,
            "reentry": hi10 > 1.15 and last["v9r"] < 1.0 and last["spx"] > last["s8"],
            "reentry_need": {"v9r_below_1": last["v9r"] < 1.0,
                             "spx_above_8d": last["spx"] > last["s8"], "s8": last["s8"]}}


def stack(v: dict) -> dict | None:
    """Variance stack for one time point: v = {VIX, DSPX, VIXEQ}. Share = VIX^2/(VIX^2+DSPX^2)."""
    a, b, c = _f(v.get("VIX")), _f(v.get("DSPX")), _f(v.get("VIXEQ"))
    if not (a > 0 and b > 0):
        return None
    tot = a * a + b * b
    return {"vix2": round(a * a, 1), "dspx2": round(b * b, 1), "tot": round(tot, 1),
            "sqrt": round(tot ** 0.5, 3), "share": round(a * a / tot, 4),
            "gap": round(tot ** 0.5 - c, 3) if c > 0 else None}


# ---------------------------------------------------------------- network + loop
class VolX:
    def __init__(self, out_dir: str, clock: Callable[[], datetime.datetime]) -> None:
        self.out, self.clock = out_dir, clock
        self.hist: dict = {}
        self.hist_day: datetime.date | None = None
        self.hist_try = 0.0
        self.q: dict = {}
        self.day: datetime.date | None = None
        self.intra: dict = {"t": []}
        self.err: str | None = None

    def _get(self, url: str, timeout: float = 8):  # noqa: ANN202
        import requests
        r = requests.get(url, timeout=timeout, headers=_UA)
        r.raise_for_status()
        return r

    def quotes(self) -> dict:
        q = {}
        for s in IDX + ETF:
            try:
                d = (self._get((_Q_IDX if s in IDX else _Q_ETF) % s).json() or {}).get("data") or {}
            except Exception as exc:
                self.err = f"{s}: {str(exc)[:80]}"
                continue
            v = _f(d.get("current_price") or d.get("last_trade_price"))
            if v > 0:
                q[s] = {"v": v, "prev": _f(d.get("prev_day_close")),
                        "t": d.get("last_trade_time") or ""}
        return q

    def history(self, today: datetime.date) -> dict:
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
            except Exception as exc:
                self.err = f"history {s}: {str(exc)[:80]}"
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
            except Exception as exc:
                self.err = f"history {s}: {str(exc)[:80]}"
        return h

    def _day_file(self, day: datetime.date) -> str:
        return os.path.join(self.out, "volx_{}.json".format(day.strftime("%Y%m%d")))

    def _load_day(self, day: datetime.date) -> None:
        self.day, self.intra = day, {"t": []}
        for s in INTRA:
            self.intra[s] = []
        try:
            with open(self._day_file(day)) as fh:
                j = json.load(fh)
        except (OSError, ValueError):
            return
        if isinstance(j, dict) and isinstance(j.get("t"), list):
            self.intra = j
            for s in INTRA:
                self.intra.setdefault(s, [None] * len(j["t"]))

    def step(self) -> None:
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
                _atomic(self._day_file(today), self.intra)
        self.write(now)

    def snapshot(self, now: datetime.datetime) -> dict:
        today = now.date()
        m = now.hour * 60 + now.minute
        final = m >= 16 * 60 + 15 or now.weekday() >= 5
        g = gauge(self.hist, self.q, today, final=final)
        pairs = zip(self.intra.get("VIX9D", []), self.intra.get("VIX", []), strict=False)
        v9 = [a / b for a, b in pairs if a and b]
        return {"asof": now.strftime("%Y-%m-%dT%H:%M:%S"), "src": SRC,
                "q": self.q, "intra": self.intra, "gauge": g[-60:] if g else None,
                "cues": cues(g, max(v9) if v9 else None) if g else None,
                "stack_now": stack({s: (self.q.get(s) or {}).get("v") for s in STK}),
                "stack_prev": stack({s: (self.q.get(s) or {}).get("prev") for s in STK}),
                "hist_through": {s: (v[-1][0].isoformat() if v else None)
                                 for s, v in self.hist.items()},
                "err": self.err}

    def write(self, now: datetime.datetime) -> None:
        os.makedirs(self.out, exist_ok=True)
        _atomic(os.path.join(self.out, "volx.json"), self.snapshot(now))

    def run(self, until_min: int | None = None) -> None:
        """One step a minute. until_min: exit after this ET minute (None = forever: thread mode)."""
        while True:
            try:
                self.err = None
                self.step()
            except Exception as exc:
                self.err = str(exc)[:200]
                print("volx error:", self.err, flush=True)
            n = self.clock()
            if until_min is not None and (n.weekday() >= 5 or n.hour * 60 + n.minute > until_min):
                print("volx: window closed - exiting", flush=True)
                return
            time.sleep(STEP_S)


def _atomic(path: str, obj: object) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(obj, fh, separators=(",", ":"))
    os.replace(tmp, path)


def start(out_dir: str, clock: Callable[[], datetime.datetime]) -> VolX:
    """Thread mode (kept for anyone embedding it; the test server no longer does)."""
    v = VolX(out_dir, clock)
    threading.Thread(target=v.run, name="volx", daemon=True).start()
    return v


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    out = DATA
    if "--out" in argv:
        out = argv[argv.index("--out") + 1]
    v = VolX(out, now_et)
    print(f"volx start {now_et():%Y-%m-%d %H:%M} -> {out}", flush=True)
    if "--once" in argv:
        v.step()
        return 0
    v.run(until_min=END_MIN)
    return 0


if __name__ == "__main__":
    sys.exit(main())
