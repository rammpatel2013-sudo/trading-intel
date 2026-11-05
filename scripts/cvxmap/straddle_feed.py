"""S1 Straddle tab feed (2026-11-04) -> data/straddle.json for the Convex cockpit.

Daily (0DTE) / Weekly (this week's last listed expiry) / Monthly (next 3rd Friday)
at-the-money straddles, each anchored at the START of its own period:
    daily   = today's 09:30 open
    weekly  = the first session of this week
    monthly = the first session after the previous monthly expiry
and the price path since that anchor, so the page can draw price inside
anchor +/- straddle (1x) and +/- 1.5x.

Zero Convex calls. Inputs:
  * idx records (decoded by cvx_live.py each minute) -> data/straddle_YYYYMMDD.ndjson
  * data/ext_raw_YYYYMMDD.ndjson.gz (the collector's every-5-min far-expiry pull,
    which carries the monthly when it is past the main pull's 12 expiries)
  * NAS DB, read-only and optional: oi_chain_daily spot for days before the feed
    existed, and an oi_chain_eod morning-book estimate when the true anchor day
    is older than the files we have. Both are labelled on the page.

Called from cvx_live.py behind try/except (like gapread): a failure here never
touches live.json or the Map.

Backfill a past day from its raw file:  python scripts/cvxmap/straddle_feed.py --day 20261102
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

SYMS = ("SPX", "SPY", "QQQ")
T0, T1 = "09:30", "16:15"
T_ANCHOR = "09:31"     # first full minute: the 09:30 quote is often the stale pre-open book
_cache = {}


def _f(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except Exception:
        return None


def _mid(d):
    b, a = _f(d.get("bid_price")), _f(d.get("ask_price"))
    if b is None or a is None or a <= 0 or b < 0 or a < b:
        return None
    return (a + b) / 2.0


def _root(code):
    r = ""
    for ch in (code or "").lstrip("."):
        if ch.isdigit():
            break
        r += ch
    return r


def strad_at(book, spot):
    """ATM straddle mid at spot, linear between the two strikes around spot."""
    ks = sorted(k for k, (c, p) in book.items() if _mid(c) is not None and _mid(p) is not None)
    lo = [k for k in ks if k <= spot]
    hi = [k for k in ks if k >= spot]
    if not lo or not hi:
        return None
    k0, k1 = lo[-1], hi[0]
    v0 = _mid(book[k0][0]) + _mid(book[k0][1])
    if k1 == k0:
        return v0
    v1 = _mid(book[k1][0]) + _mid(book[k1][1])
    return v0 + (v1 - v0) * (spot - k0) / (k1 - k0)


def strads(entry, exp_date):
    spot = _f(entry.get("spot")) or 0.0
    out, pm = {}, {}
    if spot <= 0:
        return spot, out
    for head, book in entry.get("exps") or []:
        iso = exp_date(head)
        v = strad_at(book, spot)
        if v is None:
            continue
        code = next((c.get("_sym") for c, p in book.values() if c.get("_sym")), "")
        is_pm = _root(code).endswith("W")
        if iso in out and pm.get(iso) and not is_pm:
            continue                     # keep the PM (W) series when a date is listed twice
        out[iso], pm[iso] = round(v, 3), is_pm
    return spot, out


def _line(dec, t, exp_date, ext=False):
    line = {"t": t}
    if ext:
        line["e"] = 1
    for sym in SYMS:
        if sym in dec:
            s, x = strads(dec[sym], exp_date)
            if x:
                line[sym] = {"s": round(s, 3), "x": x}
    return line if len(line) > (2 if ext else 1) else None


def _append(path, line):
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, separators=(",", ":")) + "\n")


def on_idx(dec, ts, data_dir, exp_date):
    """One idx minute (already decoded by cvx_live)."""
    t = str(ts)[11:16]
    if not (T0 <= t <= T1):
        return
    line = _line(dec, t, exp_date)
    if line:
        _append(os.path.join(data_dir, "straddle_%s.ndjson" % str(ts)[:10].replace("-", "")), line)


def _ext(data_dir, day, decode, exp_date):
    spath = os.path.join(data_dir, "straddle_state_%s.json" % day)
    try:
        stt = json.load(open(spath, encoding="utf-8"))
    except Exception:
        stt = {}
    recs, off = extraw.read_new(os.path.join(data_dir, "ext_raw_%s.ndjson.gz" % day), int(stt.get("ext_off", 0)))
    if off == stt.get("ext_off"):
        return
    out = os.path.join(data_dir, "straddle_%s.ndjson" % day)
    for rec in recs:
        t = str(rec.get("ts", ""))[11:16]
        if not (T0 <= t <= T1):
            continue
        try:
            line = _line(decode(rec), t, exp_date, ext=True)
        except Exception:
            line = None
        if line:
            _append(out, line)
    stt["ext_off"] = off
    extraw.atomic_write(spath, stt)


def _lines(data_dir, d):
    p = os.path.join(data_dir, "straddle_%s.ndjson" % d.strftime("%Y%m%d"))
    try:
        sz = os.path.getsize(p)
    except OSError:
        return []
    key = ("L", p)
    c = _cache.get(key)
    if c and c[0] == sz:
        return c[1]
    rows = []
    for ln in open(p, encoding="utf-8"):
        try:
            rows.append(json.loads(ln))
        except Exception:
            pass
    rows.sort(key=lambda r: (r.get("t", ""), r.get("e", 0)))
    _cache[key] = (sz, rows)
    return rows


# ---------------------------------------------------------------- DB (optional)
def _factory():
    if "factory" not in _cache:
        try:
            from trading_intel.config import get_settings
            from trading_intel.memory.db import make_session_factory
            _cache["factory"] = make_session_factory(get_settings())
        except Exception:
            _cache["factory"] = None
    return _cache["factory"]


def _db_closes(sym, start, end):
    key = ("C", sym, start, end)
    if key in _cache:
        return _cache[key]
    out = {}
    f = _factory()
    if f is not None:
        try:
            from sqlalchemy import select
            from trading_intel.memory.models import OiChainDaily
            with f() as s:
                for ts, spot in s.execute(select(OiChainDaily.ts, OiChainDaily.spot).where(
                        OiChainDaily.symbol == sym, OiChainDaily.ts >= start, OiChainDaily.ts <= end)):
                    if spot:
                        out[ts] = float(spot)
        except Exception:
            out = {}
    _cache[key] = out
    return out


def _ncdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _bs_strad(S, K, T, sig):
    if S <= 0 or K <= 0 or T <= 0 or sig <= 0:
        return None
    v = sig * math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * v * v) / v
    d2 = d1 - v
    c = S * _ncdf(d1) - K * _ncdf(d2)
    return c + (c - S + K)               # put by parity, r = q = 0


def _db_anchor(sym, day, expiry):
    """Estimate the straddle on `day` for `expiry` from that day's first stored book."""
    key = ("A", sym, day, expiry)
    if key in _cache:
        return _cache[key]
    res = None
    f = _factory()
    if f is not None:
        try:
            from sqlalchemy import select
            from trading_intel.memory.models import OiChainDaily, OiChainEod
            d0 = datetime.datetime.combine(day, datetime.time())
            d1 = d0 + datetime.timedelta(days=1)
            with f() as s:
                first = s.execute(select(OiChainEod.ts, OiChainEod.source).where(
                    OiChainEod.symbol == sym, OiChainEod.expiry == expiry,
                    OiChainEod.ts >= d0, OiChainEod.ts < d1).order_by(OiChainEod.ts).limit(1)).first()
                prev = s.execute(select(OiChainDaily.spot).where(
                    OiChainDaily.symbol == sym, OiChainDaily.ts < day, OiChainDaily.spot.isnot(None))
                    .order_by(OiChainDaily.ts.desc()).limit(1)).first()
                if first and prev:
                    spot = float(prev[0])
                    rows = s.execute(select(OiChainEod.strike, OiChainEod.cp, OiChainEod.iv,
                                            OiChainEod.bid_price, OiChainEod.ask_price).where(
                        OiChainEod.symbol == sym, OiChainEod.expiry == expiry,
                        OiChainEod.ts == first[0], OiChainEod.source == first[1])).all()
                    best = {}
                    for k, cp, iv, b, a in rows:
                        best.setdefault(float(k), {})[cp] = (iv, b, a)
                    ks = sorted(k for k, v in best.items() if "C" in v and "P" in v)
                    if ks:
                        k = min(ks, key=lambda x: abs(x - spot))
                        c, p = best[k]["C"], best[k]["P"]
                        if c[1] and c[2] and p[1] and p[2] and c[2] > 0 and p[2] > 0:
                            v = (c[1] + c[2]) / 2.0 + (p[1] + p[2]) / 2.0
                            how = "first stored book that day, quote mids"
                        else:
                            ivs = [x for x in (c[0], p[0]) if x]
                            iv = sum(ivs) / len(ivs) if ivs else None
                            if iv and iv > 3:
                                iv /= 100.0
                            T = max((expiry - day).days, 0.5) / 365.0
                            v = _bs_strad(spot, k, T, iv) if iv else None
                            how = "first stored book that day, ATM IV priced"
                        if v:
                            res = {"spot": round(spot, 2), "strad": round(v, 2), "how": how,
                                   "book": first[0].isoformat(timespec="minutes")}
        except Exception:
            res = None
    _cache[key] = res
    return res


# ---------------------------------------------------------------- build
def _weekdays(a, b):
    d, out = a, []
    while d <= b:
        if d.weekday() < 5:
            out.append(d)
        d += datetime.timedelta(days=1)
    return out


def _panel(sym, key, label, expiry, start, today, days_avail, data_dir):
    exp_d = datetime.date.fromisoformat(expiry)
    window = [d for d in days_avail if start <= d <= today]
    true_day = next((d for d in _weekdays(start, today)), today)
    # price + straddle path
    days, pts, anchor = [], [], None
    missing = [d for d in _weekdays(start, today) if d not in window and d < (window[0] if window else today)]
    closes = _db_closes(sym, start, today) if missing else {}
    for d in missing:
        if d in closes:
            days.append(d.isoformat())
            pts.append([len(days) - 1, "16:00", round(closes[d], 2), None, 1])
    for d in window:
        rows = _lines(data_dir, d)
        last_x, kept = None, []
        for r in rows:
            e = r.get(sym)
            if not e:
                continue
            v = e["x"].get(expiry)
            if v is not None:
                last_x = v
                if anchor is None and r["t"] >= T_ANCHOR:
                    anchor = {"date": d.isoformat(), "t": r["t"], "spot": e["s"], "strad": v, "src": "feed"}
            if r.get("e"):
                continue                 # ext lines feed the straddle only; spot path = main minutes
            t = r["t"]
            if t < T_ANCHOR:
                continue                 # 09:30 still carries the previous close (stale pre-open book)
            if d != today and int(t[3:5]) % 5 and r is not rows[-1]:
                continue
            kept.append([t, e["s"], last_x])
        if kept:
            days.append(d.isoformat())
            for t, s, x in kept:
                pts.append([len(days) - 1, t, s, x, 0])
    note = None
    if anchor is None or anchor["date"] != true_day.isoformat():
        est = _db_anchor(sym, true_day, exp_d)
        if est:
            note = "True anchor %s is older than the minute files; estimated from %s." % (true_day.isoformat(), est["how"])
            anchor = {"date": true_day.isoformat(), "t": "open", "spot": est["spot"], "strad": est["strad"],
                      "src": "est", "book": est["book"]}
        elif anchor is not None:
            note = "True anchor %s is not in our history; anchored at the first day collected (%s)." % (
                true_day.isoformat(), anchor["date"])
    if anchor is None:
        return {"key": key, "label": label, "expiry": expiry, "err": "this expiry is not in the feed yet"}
    now_s = pts[-1][2] if pts else None
    now_x = next((p[3] for p in reversed(pts) if p[3] is not None), None)
    return {"key": key, "label": label, "expiry": expiry, "dte": (exp_d - today).days,
            "start": start.isoformat(), "anchor": anchor, "note": note, "days": days, "pts": pts,
            "now": {"t": pts[-1][1] if pts else None, "spot": now_s, "strad": now_x}}


def build(data_dir, today):
    days_avail = []
    for p in glob.glob(os.path.join(data_dir, "straddle_2*.ndjson")):
        try:
            days_avail.append(datetime.datetime.strptime(os.path.basename(p)[9:17], "%Y%m%d").date())
        except Exception:
            pass
    days_avail.sort()
    out = {"asof": datetime.datetime.now().isoformat(timespec="seconds"), "today": today.isoformat(), "syms": {}}
    rows = _lines(data_dir, today)
    mon, fri = extraw.week_bounds(today)
    nm = extraw.next_monthly(today)
    pm = extraw.prev_monthly(today)
    for sym in SYMS:
        keys = sorted({k for r in rows if r.get(sym) for k in r[sym]["x"]})
        if not keys:
            out["syms"][sym] = {"err": "no straddle data for today yet"}
            continue
        ti = today.isoformat()
        daily = ti if ti in keys else next((k for k in keys if k >= ti), None)
        weekly = max((k for k in keys if mon.isoformat() <= k <= fri.isoformat()), default=None)
        monthly = nm.isoformat() if nm.isoformat() in keys else None
        panels = []
        for key, label, exp, start in (("daily", "Daily · 0DTE", daily, today),
                                       ("weekly", "Weekly", weekly, mon),
                                       ("monthly", "Monthly", monthly, pm + datetime.timedelta(days=1))):
            if not exp:
                panels.append({"key": key, "label": label, "err": "expiry not in the feed yet"})
                continue
            try:
                panels.append(_panel(sym, key, label, exp, start, today, days_avail, data_dir))
            except Exception as exc:
                panels.append({"key": key, "label": label, "expiry": exp, "err": "build error: %s" % str(exc)[:80]})
        out["syms"][sym] = {"panels": panels}
    return out


def finalize(data_dir, today, decode, exp_date):
    day = today.strftime("%Y%m%d")
    try:
        _ext(data_dir, day, decode, exp_date)
    except Exception:
        pass
    extraw.atomic_write(os.path.join(data_dir, "straddle.json"), build(data_dir, today))


def backfill(day, data_dir=None):
    """Rebuild data/straddle_<day>.ndjson from that day's raw (+ ext) files."""
    import gzip
    import cvx_live
    data_dir = data_dir or cvx_live.DATA
    out = os.path.join(data_dir, "straddle_%s.ndjson" % day)
    raw = os.path.join(data_dir, "raw_%s.ndjson.gz" % day)
    if not os.path.exists(raw):
        print("no raw file", raw)
        return 0
    if os.path.exists(out):
        os.remove(out)
    sp = os.path.join(data_dir, "straddle_state_%s.json" % day)
    if os.path.exists(sp):
        os.remove(sp)
    n = 0
    with gzip.open(raw, "rt", encoding="utf-8") as fh:
        for ln in fh:
            try:
                rec = json.loads(ln)
            except Exception:
                continue
            if rec.get("pull") != "idx":
                continue
            try:
                on_idx(cvx_live.decode(rec), rec.get("ts", ""), data_dir, cvx_live.exp_date)
                n += 1
            except Exception:
                pass
    _ext(data_dir, day, cvx_live.decode, cvx_live.exp_date)
    print("straddle backfill %s: %d minutes" % (day, n))
    return n


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--day" in args:
        backfill(args[args.index("--day") + 1])
    elif "--all" in args:
        import cvx_live
        for p in sorted(glob.glob(os.path.join(cvx_live.DATA, "raw_2*.ndjson.gz"))):
            d = os.path.basename(p)[4:12]
            if not os.path.exists(os.path.join(cvx_live.DATA, "straddle_%s.ndjson" % d)):
                backfill(d)
    if "--build" in args or not args:
        import cvx_live
        t = cvx_live.today_et()
        extraw.atomic_write(os.path.join(cvx_live.DATA, "straddle.json"), build(cvx_live.DATA, t))
        print("wrote straddle.json for", t)
