"""range_feed.py - created 2026-11-13 for Mithil. Feeds the cockpit's Range card (main page,
button "Range" next to Pulse / Robustness, OFF by default).

[Mithil 2026-11-12 22:46] QuantNug's "How big is today likely to be? (VIX + curve)" card: "adding to
cockpit might be helpful. But we can test also". Plan approved 22:5x (Range card on the main page).

WHAT THE CARD SHOWS (SPX points, regular-hours high to low)
  Morning   typical / quiet (1 day in 10 is smaller) / big (1 in 10 is bigger) / 1 in 50, for the
            next session, from the prior close of VIX and of VIX / 3-month VIX ("the curve").
  Live      (computed in the page) range so far, the share of its range a typical day has made by
            now, "on pace for", and a best guess for the full day with a 1-in-10 band that narrows
            through the day.
  Recent    the last sessions, typical -> actual.
  Scores    how often the bands held: 2018 to now (out of sample) and live since 2026-11-13.

MODEL range-v1 (frozen 2026-11-13; a change makes v2 and applies forward only)
  log(range / prior SPX close) = B0 + B1 * log VIX + B2 * log(VIX / VIX3M)   (prior-session closes)
  Fit 2010-01-04 .. 2017-12-29 (2,013 sessions), checked 2018-01-02 .. 2026-11-12 (2,229 sessions):
  R^2 0.535 fit / 0.505 check (VIX level alone: 0.458 check). Quantiles of the fit's error:
  Q10 / Q50 / Q90 / Q98. On the check years 13.6% of days came in under "quiet", 8.5% over "big",
  1.4% over "1 in 50", 77.9% inside the 1-in-10 band, 4.1% past twice "typical". It reproduces
  QuantNug's 11/12 card (typical 72 / quiet 44 / big 123 / 1 in 50 177): ours 73.6 / 44.6 / 125.5 /
  179.1, and his recent days (11/04 100, 11/05 66, 11/06 164, 11/09 135, 11/10 90, 11/11 87): ours
  99.9 / 67.1 / 163.8 / 135.5 / 91.2 / 86.7. Data: Cboe's free daily history (VIX, VIX3M CSV; SPX
  OHLC json).

PACE (how much of its range a session has usually made by each minute)
  data/range_pace.json keeps, per session, the share of the day's range made by 5, 10, ... 390
  minutes after 09:30 (78 marks). Sources: our own SPX minute log (data/flow_YYYYMMDD.ndjson, added
  every night; minute prices) and, once, SPY 1-minute bars from CVForge (--pace, ~2 years; bar
  highs and lows). The same date from our log replaces the SPY one. Early-close days are left out.
  Curve = median share at each mark. Best guess G = exp(w * ln(pace) + (1 - w) * ln(typical)),
  never below the range so far; pace = so far / curve. w per mark = least squares through the
  origin of ln(actual / typical) on ln(pace / typical) over the stored sessions with a Cboe range,
  kept in 0..1. Band = 10th / 90th percentile of ln(actual / G) at that mark. Fewer than 60 such
  sessions = "rough": w = curve, band = the morning band.

FILES (scripts/cvxmap/data/, served by cvxmap-serve)
  range_card.json   what the page reads          range_pace.json   the per-session store

RUN (NAS, DSM task "TI range feed", Mon-Fri 17:45 ET, user root):
    bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_script.sh range_feed
  Once after the ship (pulls ~2 years of SPY minute bars from CVForge, then writes the card):
    bash .../scripts/nas/run_script.sh range_feed --pace
  If today's Cboe close is not out yet the run waits (10 min, up to 6 times), then writes what it
  has.

Descriptive only (rule 4): size, never direction. No AI (rule 7). Network: Cboe's free files;
CVForge only with --pace.
"""

from __future__ import annotations

import argparse
import datetime as dt
import itertools
import json
import math
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CVX = os.path.join(HERE, "cvxmap")
DATA = os.path.join(CVX, "data")
sys.path.insert(0, CVX)
import market_cal  # noqa: E402  (scripts/cvxmap/market_cal.py: NYSE sessions + early closes)

V = "range-v1"
B0, B1, B2 = -7.747636256637671, 1.1419458363749766, 1.5010746044632652
Q10, Q50, Q90, Q98 = (
    -0.518153172544791,
    -0.016261624470365277,
    0.5164226199875324,
    0.8723207245297594,
)
MODEL_INFO = {
    "fit": "2010-01-04..2017-12-29",
    "n_fit": 2013,
    "check": "2018-01-02..2026-11-12",
    "n_check": 2229,
    "r2_fit": 0.535,
    "r2_check": 0.505,
    "r2_check_vix_only": 0.458,
    "check_under_quiet": 0.136,
    "check_over_big": 0.085,
    "check_over_1in50": 0.014,
    "check_in_band": 0.779,
    "check_over_2x": 0.041,
}
OOS_FROM = dt.date(2018, 1, 1)
LIVE_FROM = dt.date(2026, 11, 13)
OPEN = 9 * 60 + 30
MARKS = list(range(5, 391, 5))  # minutes after 09:30
MIN_FIT = 60
SCORE_MARKS = (30, 60, 90, 150, 270)  # 10:00, 10:30, 11:00, 12:00, 14:00
URL_CSV = "https://cdn.cboe.com/api/global/us_indices/daily_prices/%s_History.csv"
URL_OHLC = "https://cdn.cboe.com/api/global/delayed_quotes/charts/historical/_%s.json"
_UA = {"User-Agent": "Mozilla/5.0"}  # same header volx.py sends to the same Cboe CDN


# ------------------------------------------------------------------ pure maths (no network)
def forecast(vix: float, vix3m: float, prev_close: float) -> dict | None:
    """The morning numbers (SPX points) from the prior-session closes."""
    if not (vix > 0 and vix3m > 0 and prev_close > 0):
        return None
    mu = B0 + B1 * math.log(vix) + B2 * math.log(vix / vix3m)
    f = lambda q: round(math.exp(mu + q) * prev_close, 1)  # noqa: E731
    return {"typical": f(Q50), "quiet": f(Q10), "big": f(Q90), "p98": f(Q98), "mu": mu}


def parse_csv(text: str) -> dict:
    """Cboe *_History.csv -> {date: close} (last column)."""
    out = {}
    for line in text.splitlines()[1:]:
        p = line.strip().split(",")
        if len(p) < 2:
            continue
        try:
            m, d, y = p[0].split("/")
            c = float(p[-1])
        except ValueError:
            continue
        if c > 0:
            out[dt.date(int(y), int(m), int(d))] = c
    return out


def parse_ohlc(js: dict) -> dict:
    """Cboe charts/historical json -> {date: (open, high, low, close)}; high <= low dropped."""
    out = {}
    for x in (js or {}).get("data") or []:
        try:
            d = dt.date.fromisoformat(str(x["date"])[:10])
            o, h, lo, c = (float(x[k]) for k in ("open", "high", "low", "close"))
        except (KeyError, TypeError, ValueError):
            continue
        if h > lo > 0 and c > 0:
            out[d] = (o, h, lo, c)
    return out


def sessions_in(ohlc: dict, vix: dict, v3: dict) -> list:
    return sorted(d for d in ohlc if d in vix and d in v3)


def day_forecasts(ohlc: dict, vix: dict, v3: dict) -> dict:
    """{date: (forecast, actual range)} for each session whose prior session has all 3 closes."""
    ds = sessions_in(ohlc, vix, v3)
    out = {}
    for a, b in itertools.pairwise(ds):
        f = forecast(vix[a], v3[a], ohlc[a][3])
        if f:
            out[b] = (f, ohlc[b][1] - ohlc[b][2])
    return out


def band_stats(pairs: list) -> dict:
    """pairs = [(forecast, actual)] -> counts the card shows."""
    n = len(pairs)
    if not n:
        return {"n": 0}
    uq = sum(1 for f, a in pairs if a < f["quiet"])
    ob = sum(1 for f, a in pairs if a > f["big"])
    o98 = sum(1 for f, a in pairs if a > f["p98"])
    o2x = sum(1 for f, a in pairs if a > 2 * f["typical"])
    return {
        "n": n,
        "under_quiet": uq,
        "over_big": ob,
        "over_1in50": o98,
        "over_2x": o2x,
        "in_band": n - uq - ob,
        "in_band_pct": round(100.0 * (n - uq - ob) / n, 1),
    }


def shares_from_prices(px: dict, close_min: int) -> list | None:
    """px = {minute of day: price} (minute snapshots). Share of the full range by each mark.
    The 09:30 snapshot is left out: the minute log still carries the prior close there."""
    end = close_min - OPEN
    if end != 390:
        return None
    ms = sorted(m for m in px if OPEN < m <= close_min and px[m] > 0)
    if len(ms) < 350:
        return None
    lo = hi = px[ms[0]]
    run, j = [], 0
    for mk in MARKS:
        while j < len(ms) and ms[j] <= OPEN + mk:
            x = px[ms[j]]
            lo, hi = min(lo, x), max(hi, x)
            j += 1
        run.append(hi - lo)
    full = run[-1]
    if not full > 0:
        return None
    return [round(r / full, 4) for r in run]


def shares_from_bars(bars: list, close_min: int) -> list | None:
    """bars = [(start minute, high, low)] (1-minute). A bar starting before 09:30 + mark counts."""
    if close_min - OPEN != 390:
        return None
    bs = sorted(b for b in bars if OPEN <= b[0] < close_min and b[1] >= b[2] > 0)
    if len(bs) < 350:
        return None
    lo, hi = bs[0][2], bs[0][1]
    run, j = [], 0
    for mk in MARKS:
        while j < len(bs) and bs[j][0] < OPEN + mk:
            lo, hi = min(lo, bs[j][2]), max(hi, bs[j][1])
            j += 1
        run.append(hi - lo)
    full = run[-1]
    if not full > 0:
        return None
    return [round(r / full, 4) for r in run]


def _q(vals: list, p: float) -> float:
    s = sorted(vals)
    if not s:
        return float("nan")
    k = (len(s) - 1) * p
    i = math.floor(k)
    j = min(i + 1, len(s) - 1)
    return s[i] + (s[j] - s[i]) * (k - i)


def guess(typ: float, curve_m: float, w_m: float, so_far: float) -> float:
    """Best guess for the full-day range: logs blended, never below the range so far."""
    if not (so_far > 0 and curve_m > 0):
        return typ
    pace = so_far / curve_m
    return max(so_far, math.exp(w_m * math.log(pace) + (1 - w_m) * math.log(typ)))


def build_pace(days: dict, fc: dict) -> dict:
    """days = {"YYYY-MM-DD": {"sh": [78], "src": ...}}; fc = {"YYYY-MM-DD": (forecast, actual)}.
    Same keys. Returns the curve, the blend weights and the band per mark, plus how many sessions
    fed each."""
    if not days:
        return {"n": 0, "n_fit": 0, "rough": True}
    shs = [d["sh"] for d in days.values() if d.get("sh") and len(d["sh"]) == len(MARKS)]
    curve = [round(statistics.median(s[i] for s in shs), 4) for i in range(len(MARKS))]
    fit = [
        (days[k]["sh"], fc[k][0]["typical"], fc[k][1])
        for k in sorted(days)
        if k in fc and len(days[k].get("sh") or []) == len(MARKS)
    ]
    src = {}
    for d in days.values():
        src[d.get("src", "?")] = src.get(d.get("src", "?"), 0) + 1
    out = {
        "n": len(shs),
        "n_fit": len(fit),
        "src": src,
        "curve": curve,
        "rough": len(fit) < MIN_FIT,
    }
    if out["rough"]:
        out["w"] = curve[:]
        out["lo"] = [round(Q10 - Q50, 4)] * len(MARKS)
        out["hi"] = [round(Q90 - Q50, 4)] * len(MARKS)
        return out
    w, lo, hi = [], [], []
    for i, c in enumerate(curve):
        sxy = sxx = 0.0
        for sh, typ, rng in fit:
            y = math.log(rng / typ)
            x = math.log(max(sh[i], 1e-4) / c) + y
            sxy += x * y
            sxx += x * x
        wi = min(1.0, max(0.0, sxy / sxx)) if sxx > 0 else 0.0
        errs = [math.log(rng / guess(typ, c, wi, sh[i] * rng)) for sh, typ, rng in fit]
        w.append(round(wi, 4))
        lo.append(round(_q(errs, 0.10), 4))
        hi.append(round(_q(errs, 0.90), 4))
    out.update(w=w, lo=lo, hi=hi)
    return out


def pace_scores(days: dict, fc: dict, pace: dict, since: dt.date | None = None) -> dict:
    """At 10:00 / 10:30 / 11:00 / 12:00 / 14:00: on how many sessions the best guess was closer to
    the actual range than the morning typical, and the median miss of each (points)."""
    if not pace.get("curve"):
        return {}
    res = {}
    for mk in SCORE_MARKS:
        i = MARKS.index(mk)
        n = better = 0
        eg, et = [], []
        for k in sorted(days):
            if since and k < since.isoformat():
                continue
            if k not in fc or len(days[k].get("sh") or []) != len(MARKS):
                continue
            f, rng = fc[k]
            typ = f["typical"]
            g = guess(typ, pace["curve"][i], pace["w"][i], days[k]["sh"][i] * rng)
            n += 1
            better += abs(math.log(rng / g)) < abs(math.log(rng / typ))
            eg.append(abs(rng - g))
            et.append(abs(rng - typ))
        res[f"{(OPEN + mk) // 60}:{(OPEN + mk) % 60:02d}"] = {
            "n": n,
            "guess_closer": better,
            "median_miss_guess": round(statistics.median(eg), 1) if eg else None,
            "median_miss_typical": round(statistics.median(et), 1) if et else None,
        }
    return res


def next_session(d: dt.date) -> dt.date:
    x = d + dt.timedelta(days=1)
    while not market_cal.is_session(x):
        x += dt.timedelta(days=1)
    return x


def prev_session(d: dt.date) -> dt.date:
    x = d - dt.timedelta(days=1)
    while not market_cal.is_session(x):
        x -= dt.timedelta(days=1)
    return x


def close_min_of(d: dt.date) -> int:
    h, m = market_cal.session_close(d)
    return h * 60 + m


def make_card(vix: dict, v3: dict, ohlc: dict, days: dict, today: dt.date, now_txt: str) -> dict:
    fc = day_forecasts(ohlc, vix, v3)
    ds = sessions_in(ohlc, vix, v3)
    last = ds[-1] if ds else None
    card = {
        "v": V,
        "generated": now_txt,
        "model": dict(MODEL_INFO, b=[B0, B1, B2], q=[Q10, Q50, Q90, Q98]),
        "units": "SPX points, regular-hours high to low",
        "source": "Cboe daily history (free); pace: our SPX minute log + CVForge SPY 1-min bars",
    }
    if last is None:
        card["error"] = "no Cboe history"
        return card
    nxt = next_session(last)
    f = forecast(vix[last], v3[last], ohlc[last][3])
    card.update(
        for_date=nxt.isoformat(),
        inputs_date=last.isoformat(),
        vix=vix[last],
        vix3m=v3[last],
        ratio=round(vix[last] / v3[last], 4),
        prev_close=ohlc[last][3],
        early_close=market_cal.session_close(nxt) != (16, 0),
    )
    if f:
        card.update(typical=f["typical"], quiet=f["quiet"], big=f["big"], p98=f["p98"])
    rec = []
    for d in ds[-6:]:
        if d in fc:
            fd, rng = fc[d]
            rec.append(
                {
                    "date": d.isoformat(),
                    "typical": fd["typical"],
                    "actual": round(rng, 1),
                    "quiet": fd["quiet"],
                    "big": fd["big"],
                }
            )
    card["recent"] = rec
    pairs_oos = [fc[d] for d in sorted(fc) if d >= OOS_FROM]
    pairs_live = [fc[d] for d in sorted(fc) if d >= LIVE_FROM]
    card["scores"] = {
        "oos": dict(band_stats(pairs_oos), since=OOS_FROM.isoformat()),
        "live": dict(band_stats(pairs_live), since=LIVE_FROM.isoformat()),
    }
    fci = {k.isoformat(): v for k, v in fc.items()}  # the pace store is keyed by ISO date strings
    pace = build_pace(days, fci)
    card["pace"] = {
        k: pace.get(k) for k in ("n", "n_fit", "src", "rough", "curve", "w", "lo", "hi")
    }
    card["pace"]["marks"] = MARKS
    if not pace.get("rough"):
        card["scores"]["pace_stored"] = pace_scores(days, fci, pace)
        card["scores"]["pace_live"] = pace_scores(
            {k: v for k, v in days.items() if v.get("src") == "spx_log"}, fci, pace, LIVE_FROM
        )
    return card


# ------------------------------------------------------------------ files + network
def _atomic(path: str, obj: object) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, separators=(",", ":"))
    os.replace(tmp, path)


def load_store(data: str) -> dict:
    try:
        with open(os.path.join(data, "range_pace.json"), encoding="utf-8") as fh:
            j = json.load(fh)
        return (
            j if isinstance(j, dict) and isinstance(j.get("days"), dict) else {"v": V, "days": {}}
        )
    except (OSError, ValueError):
        return {"v": V, "days": {}}


def log_prices(path: str) -> dict:
    """SPX {minute: price} from one minute log (last record per minute wins)."""
    px = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if '"SPX"' not in line:
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if o.get("s") != "SPX" or not (o.get("x") or 0) > 0:
                continue
            h, m = str(o.get("t", ""))[:5].split(":")
            px[int(h) * 60 + int(m)] = float(o["x"])
    return px


def add_logs(store: dict, data: str, today: dt.date) -> list:
    """Add every finished session's SPX minute log that the store doesn't have from the log yet."""
    added = []
    for name in sorted(os.listdir(data)):
        if not (name.startswith("flow_") and name.endswith(".ndjson") and len(name) == 20):
            continue
        try:
            d = dt.datetime.strptime(name[5:13], "%Y%m%d").date()
        except ValueError:
            continue
        key = d.isoformat()
        if (
            d > today
            or (store["days"].get(key) or {}).get("src") == "spx_log"
            or not market_cal.is_session(d)
        ):
            continue
        sh = shares_from_prices(log_prices(os.path.join(data, name)), close_min_of(d))
        if sh:
            store["days"][key] = {"src": "spx_log", "sh": sh}
            added.append(key)
    return added


def _get(url: str, timeout: float = 30):  # noqa: ANN202
    import httpx  # a declared dependency (requests is not); Cboe's CDN answers with a redirect

    r = httpx.get(url, timeout=timeout, headers=_UA, follow_redirects=True)
    r.raise_for_status()
    return r


def fetch_cboe() -> tuple:
    vix = parse_csv(_get(URL_CSV % "VIX").text)
    v3 = parse_csv(_get(URL_CSV % "VIX3M").text)
    ohlc = parse_ohlc(_get(URL_OHLC % "SPX", timeout=60).json())
    return vix, v3, ohlc


def pull_spy_pace(store: dict, years: float = 2.0, client=None, sleep: float = 0.4) -> dict:  # noqa: ANN001
    """One-off: SPY 1-minute bars from CVForge /mas -> per-session shares (our log days kept)."""
    from zoneinfo import ZoneInfo

    et = ZoneInfo("America/New_York")
    if client is None:
        from trading_intel.clients.cvforge import CVForgeClient
        from trading_intel.config import Settings

        client = CVForgeClient(Settings())
    end = dt.date.today()
    start = end - dt.timedelta(days=int(365 * years))
    bars_by_day: dict = {}
    a, calls, errs = start, 0, []
    while a <= end:
        b = min(end, a + dt.timedelta(days=9))
        try:
            df = client.aggs(
                "SPY",
                frm=a.isoformat(),
                to=b.isoformat(),
                multiplier=1,
                timespan="minute",
                limit=5000,
            )
            calls += 1
        except Exception as exc:
            errs.append(f"{a}: {str(exc)[:80]}")
            df = None
        if df is not None and len(df) and {"t", "h", "l"} <= set(df.columns):
            for t, h, lo in zip(df["t"], df["h"], df["l"], strict=False):
                ts = dt.datetime.fromtimestamp(int(t) / 1000, tz=dt.UTC).astimezone(et)
                bars_by_day.setdefault(ts.date(), []).append(
                    (ts.hour * 60 + ts.minute, float(h), float(lo))
                )
        a = b + dt.timedelta(days=1)
        time.sleep(sleep)
    added = 0
    for d, bars in bars_by_day.items():
        key = d.isoformat()
        if (store["days"].get(key) or {}).get("src") == "spx_log" or not market_cal.is_session(d):
            continue
        sh = shares_from_bars(bars, close_min_of(d))
        if sh:
            store["days"][key] = {"src": "spy_1m", "sh": sh}
            added += 1
    return {
        "calls": calls,
        "days_with_bars": len(bars_by_day),
        "added": added,
        "errors": errs[:5],
        "n_errors": len(errs),
    }


def run(
    data: str,
    today: dt.date,
    pace: bool,
    wait: bool,
    fetch: object = fetch_cboe,
    now_txt: str | None = None,
) -> dict:
    store = load_store(data)
    info = {}
    if pace:
        info["pace_pull"] = pull_spy_pace(store)
    info["logs_added"] = add_logs(store, data, today)
    tries = 0
    while True:
        vix, v3, ohlc = fetch()
        want = today if market_cal.is_session(today) else prev_session(today)
        have = max(sessions_in(ohlc, vix, v3) or [dt.date(1900, 1, 1)])
        if have >= want or not wait or tries >= 6:
            break
        tries += 1
        print(
            f"range_feed: Cboe history ends {have}, want {want}; waiting 10 min ({tries}/6)",
            flush=True,
        )
        time.sleep(600)
    store["v"] = V
    _atomic(os.path.join(data, "range_pace.json"), store)
    card = make_card(
        vix, v3, ohlc, store["days"], today, now_txt or dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    )
    card["feed"] = info
    _atomic(os.path.join(data, "range_card.json"), card)
    return card


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--pace",
        action="store_true",
        help="also pull ~2 years of SPY 1-minute bars from CVForge (one-off)",
    )
    ap.add_argument("--data", default=DATA)
    ap.add_argument("--no-wait", action="store_true", help="don't wait for today's Cboe close")
    a = ap.parse_args(argv)
    from zoneinfo import ZoneInfo

    now = dt.datetime.now(ZoneInfo("America/New_York"))
    card = run(a.data, now.date(), a.pace, not a.no_wait, now_txt=now.strftime("%Y-%m-%d %H:%M"))
    p = card.get("pace") or {}
    sc = (card.get("scores") or {}).get("oos") or {}
    i30 = MARKS.index(30)
    curve, w = p.get("curve") or [None] * 78, p.get("w") or [None] * 78
    print(
        f"range_feed.done for={card.get('for_date')} inputs={card.get('inputs_date')} "
        f"VIX={card.get('vix')} VIX3M={card.get('vix3m')} typical={card.get('typical')} "
        f"quiet={card.get('quiet')} big={card.get('big')} p98={card.get('p98')} | "
        f"pace n={p.get('n')} fit={p.get('n_fit')} rough={p.get('rough')} src={p.get('src')} "
        f"share@10:00={curve[i30]} w@10:00={w[i30]} | "
        f"oos in band {sc.get('in_band_pct')}% n={sc.get('n')} | "
        f"feed={json.dumps(card.get('feed'))[:300]}",
        flush=True,
    )
    return 0 if card.get("typical") else 1


if __name__ == "__main__":
    sys.exit(main())
