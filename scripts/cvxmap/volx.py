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

2026-11-14 VOL BOARD (vol_board.html, served at :8899/vol_board.html): volx.json gains "board" =
  rows  13 more free Cboe vol indices (VXN RVX VXD VIXEQ SKEW VXTLT OVX GVZ VXSLV VXGDX VXEEM
        VXEWZ VXAZN): level, 1/5/21-day change, 1-year percentile, ratio to VIX + its percentile
  etp   the vol funds (UVXY UVIX VXX VIXY VIXM SVXY SVIX): price and 1/5/21-day % change
  fade  descriptor of the spike-fade set-ups tested 11/14 (claude/vol-etp-backtest-20261114.md):
        VIX ladder bucket, last first-close >= 25 / >= 30, VIX9D/VIX run + last fear release
  bt    the backtest numbers the page prints next to them (frozen 2026-11-14)
  Board quotes refresh every 5 min (minute quotes reused where they exist); history once a day.
  main() copies scripts/cvxmap/vol_board.html into the data folder (like flow_orbs.html).
  Descriptive only (rule 4): no alert, no trade.

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
# 2026-11-14 vol board: (symbol, plain name, group)
XV = (("VXN", "Nasdaq 100", "Equity"), ("RVX", "Russell 2000", "Equity"),
      ("VXD", "Dow 30", "Equity"), ("VIXEQ", "S&P single stocks (avg)", "Equity"),
      ("SKEW", "S&P crash-tail price", "Equity"),
      ("VXTLT", "Treasury bonds (TLT)", "Rates"), ("OVX", "Crude oil", "Commodities"),
      ("GVZ", "Gold", "Commodities"), ("VXSLV", "Silver", "Commodities"),
      ("VXGDX", "Gold miners", "Commodities"), ("VXEEM", "Emerging markets", "World"),
      ("VXEWZ", "Brazil", "World"), ("VXAZN", "Amazon", "Single stock"))
XV_SYMS = tuple(s for s, _, _ in XV)
# (symbol, what it holds, daily leverage to short-term VIX futures)
VETP = (("UVXY", "1.5x long VIX futures", 1.5), ("UVIX", "2x long VIX futures", 2.0),
        ("VXX", "1x long VIX futures (note)", 1.0), ("VIXY", "1x long VIX futures", 1.0),
        ("VIXM", "1x mid-term VIX futures", 1.0), ("SVXY", "-0.5x short VIX futures", -0.5),
        ("SVIX", "-1x short VIX futures", -1.0))
VETP_SYMS = tuple(s for s, _, _ in VETP)
B_IDX = tuple(s for s in (*XV_SYMS, "VIX3M") if s not in IDX)       # board-only index quotes
B_ETF = tuple(s for s in VETP_SYMS if s not in ETF)                  # board-only fund quotes
BOARD_EVERY_S = 300
PAGE = "vol_board.html"
# VIX ladder (VIX level -> VIX lower ~84 sessions later; Cboe closes 2004-2026; ledger D, 11/14)
LADDER = ((20.0, "under 20", "no spike to fade; the funds bleed slowly in calm markets"),
          (30.0, "20 to 30", "VIX was lower 4 months later 76% of the time (median -16%)"),
          (40.0, "30 to 40", "lower 4 months later 94% of the time (median -31%)"),
          (50.0, "40 to 50", "lower 4 months later 99% of the time (median -38%)"),
          (1e9, "50 and up", "lower 4 months later 100% of the time (median -36%)"))
# Vol-fund backtest 2026-11-14 (claude/vol-etp-backtest-20261114.md): entry next open, 60 sessions,
# short = static short incl. 5%/yr borrow; UVXY/SVXY from 2018-03 (current leverage), UVIX/SVIX from
# 2022-04. path = worst move AGAINST the position during the hold. Frozen numbers, not live.
BT = {"asof": "2026-11-14", "hold": 60, "rows": [
    ["any day (baseline)", "short UVXY", 2162, 12.0, 80, -787, 1192],
    ["any day (baseline)", "long SVXY", 2162, 4.0, 69, -60, 66],
    ["any day (baseline)", "long SVIX", 1132, 8.7, 67, -61, 66],
    ["VIX first close >= 25", "short UVXY", 19, 20.5, 84, -156, 804],
    ["VIX first close >= 25", "long SVXY", 19, 5.5, 68, -45, 60],
    ["VIX first close >= 25", "long SVIX", 10, 14.8, 70, -22, 51],
    ["VIX first close >= 30", "short UVXY", 12, 36.2, 92, -40, 431],
    ["VIX first close >= 30", "long SVXY", 12, 11.9, 75, -31, 51],
    ["VIX first close >= 30", "long SVIX", 5, 23.9, 100, 8, 33],
    ["fear release (VIX9D/VIX)", "short UVXY", 59, 15.6, 86, -408, 1060],
    ["fear release (VIX9D/VIX)", "long SVXY", 59, 6.9, 75, -50, 64],
    ["fear release (VIX9D/VIX)", "long SVIX", 34, 19.2, 85, -46, 64]],
    "cols": ["entry", "trade", "n", "avg %", "win %", "worst %", "worst path %"]}
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


def _pct(vals: list[float], now: float) -> float | None:
    """Percentile of `now` inside `vals` (ties count half)."""
    if not vals:
        return None
    below = sum(1 for x in vals if x < now) + 0.5 * sum(1 for x in vals if x == now)
    return round(100.0 * below / len(vals), 1)


def _level(ser: list, x: dict | None, today: datetime.date) -> tuple[float, str, float, bool]:
    """(value, source, previous close, fresh). fresh = the value is NOT in the history yet.
    A quote whose session is already in the history is that close (history wins). A quote from a
    finished session the history doesn't have yet (Cboe's fund history lags a day) uses the quote's
    `close` field: after the bell `current_price` can be a stale mid (UVXY 11/13: 19.00 vs 17.99).
    The previous close always comes from the history, never the quote (it rolls after hours)."""
    v = _f((x or {}).get("v"))
    last = ser[-1][0] if ser else None
    if v > 0:
        d = qday(x, today)
        if last is None or d > last:
            c = _f((x or {}).get("c"))
            val = c if (d < today and c > 0) else v
            return val, ("live" if d == today else "close"), (ser[-1][1] if ser else 0.0), True
    if ser:
        return ser[-1][1], "close", (ser[-2][1] if len(ser) >= 2 else 0.0), False
    return 0.0, "", 0.0, False


def _spark(ser: list, v: float, fresh: bool) -> list[float]:
    """Last 20 closes, plus the current value when the history doesn't hold it yet."""
    return [round(c, 2) for _, c in ser[-20:]] + ([round(v, 2)] if fresh else [])


def _back(ser: list, n: int, fresh: bool) -> float | None:
    """The close n sessions before the current value."""
    k = n if fresh else n + 1
    return ser[-k][1] if len(ser) >= k else None


def fade_state(hist: dict, q: dict, today: datetime.date) -> dict | None:
    """The spike-fade set-ups, as states (descriptors): VIX ladder bucket, the last FIRST close at
    or above 25 / 30 (no other close at that level in the 20 sessions before it), the current run
    of VIX9D/VIX closes above 1.00 and the last fear release (a run of >= 3 that ended <= 1.00)."""
    vser = list(hist.get("VIX", []))
    v9 = dict(hist.get("VIX9D", []))
    vq = q.get("VIX") or {}
    v = _f(vq.get("v"))
    d = qday(vq, today)
    live = v > 0 and (not vser or vser[-1][0] < d)
    if live:
        vser.append((d, v))
        v9now = _f((q.get("VIX9D") or {}).get("v"))
        if v9now > 0 and d not in v9:
            v9[d] = v9now
    if not vser:
        return None
    vix = vser[-1][1]
    band = next(b for b in LADDER if vix < b[0])

    def first_close(level: float) -> dict | None:
        hits = [i for i, (_, c) in enumerate(vser) if c >= level]
        starts = [i for k, i in enumerate(hits) if k == 0 or i - hits[k - 1] > 20]
        if not starts:
            return None
        i = starts[-1]
        return {"d": vser[i][0].isoformat(), "vix": round(vser[i][1], 2), "ago": len(vser) - 1 - i}

    run, last = 0, None
    ratios = [(dd, v9[dd] / c) for dd, c in vser if v9.get(dd) and c > 0]
    for dd, r in ratios:
        if r > 1.0:
            run += 1
        else:
            if run >= 3:
                last = {"d": dd.isoformat(), "days": run}
            run = 0
    v3 = _f((q.get("VIX3M") or {}).get("v")) or (hist.get("VIX3M") or [(None, 0.0)])[-1][1]
    return {"vix": round(vix, 2), "live": live, "bucket": band[1], "bucket_note": band[2],
            "c25": first_close(25.0), "c30": first_close(30.0),
            "v9r": round(ratios[-1][1], 3) if ratios else None, "run_above1": run, "release": last,
            "vix_vix3m": round(vix / v3, 3) if v3 else None}


def board(hist: dict, q: dict, today: datetime.date) -> dict:
    """Vol board for vol_board.html. q = every quote we hold (minute + 5-minute board quotes)."""
    vix_now = _f((q.get("VIX") or {}).get("v")) or ((hist.get("VIX") or [(None, 0.0)])[-1][1])
    vh = dict(hist.get("VIX", []))
    rows = []
    for s, name, grp in XV:
        ser = hist.get(s, [])
        v, src, prev, fresh = _level(ser, q.get(s), today)
        if v <= 0:
            continue
        yr = [c for _, c in ser[-252:]]
        b5, b21 = _back(ser, 5, fresh), _back(ser, 21, fresh)
        row = {"s": s, "name": name, "grp": grp, "v": round(v, 2), "src": src,
               "d1": round(v - prev, 2) if prev else None,
               "d5": round(v - b5, 2) if b5 else None,
               "d21": round(v - b21, 2) if b21 else None,
               "pct": _pct(yr, v) if len(yr) >= 60 else None,
               "spark": _spark(ser, v, fresh), "ratio": None, "ratio_pct": None}
        if s != "SKEW" and vix_now > 0:
            rat = [c / vh[dd] for dd, c in ser[-252:] if vh.get(dd)]
            row["ratio"] = round(v / vix_now, 3)
            row["ratio_pct"] = _pct(rat, v / vix_now) if len(rat) >= 60 else None
        rows.append(row)
    etp = []
    for s, name, lev in VETP:
        ser = hist.get(s, [])
        v, src, prev, fresh = _level(ser, q.get(s), today)
        if v <= 0:
            continue

        def chg(base: float | None, v: float = v) -> float | None:
            return round(100.0 * (v / base - 1.0), 2) if base else None

        etp.append({"s": s, "name": name, "lev": lev, "v": round(v, 2), "src": src, "d1": chg(prev),
                    "d5": chg(_back(ser, 5, fresh)), "d21": chg(_back(ser, 21, fresh)),
                    "spark": _spark(ser, v, fresh)})
    return {"rows": rows, "etp": etp, "fade": fade_state(hist, q, today), "bt": BT}


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
        self.bq: dict = {}              # 2026-11-14 board-only quotes (every BOARD_EVERY_S)
        self.bq_t = 0.0

    def _get(self, url: str, timeout: float = 8):  # noqa: ANN202
        import requests
        r = requests.get(url, timeout=timeout, headers=_UA)
        r.raise_for_status()
        return r

    def quotes(self, idx: tuple = IDX, etf: tuple = ETF) -> dict:
        q = {}
        for s in idx + etf:
            try:
                d = (self._get((_Q_IDX if s in idx else _Q_ETF) % s).json() or {}).get("data") or {}
            except Exception as exc:
                self.err = f"{s}: {str(exc)[:80]}"
                continue
            v = _f(d.get("current_price") or d.get("last_trade_price"))
            if v > 0:
                q[s] = {"v": v, "prev": _f(d.get("prev_day_close")),
                        "t": d.get("last_trade_time") or "", "c": _f(d.get("close"))}
        return q

    def history(self, today: datetime.date) -> dict:
        h = {}
        for s in (*H_IDX, *XV_SYMS, "VIX3M"):
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
        for s in ETF + B_ETF:
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
        if time.time() - self.bq_t > BOARD_EVERY_S:
            self.bq_t = time.time()
            bq = self.quotes(B_IDX, B_ETF)
            if bq:
                self.bq = bq
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
                "board": _safe_board(self.hist, {**self.bq, **self.q}, today),
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


def _safe_board(hist: dict, q: dict, today: datetime.date) -> dict | None:
    """The board never takes volx.json down with it."""
    try:
        return board(hist, q, today)
    except Exception as exc:  # descriptive extra: log and carry on
        print("volx board error:", str(exc)[:200], flush=True)
        return None


def copy_page(out_dir: str) -> bool:
    """Put scripts/cvxmap/vol_board.html next to volx.json so cvxmap-serve serves it."""
    src = os.path.join(HERE, PAGE)
    if not os.path.exists(src):
        return False
    try:
        import shutil
        os.makedirs(out_dir, exist_ok=True)
        shutil.copyfile(src, os.path.join(out_dir, PAGE))
        return True
    except OSError as exc:
        print("volx: page copy failed:", str(exc)[:120], flush=True)
        return False


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
    copy_page(out)
    if "--once" in argv:
        v.step()
        return 0
    v.run(until_min=END_MIN)
    return 0


if __name__ == "__main__":
    sys.exit(main())
