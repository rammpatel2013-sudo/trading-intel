"""
gapread.py - created 2026-11-04 (Phase 1, Mithil's opening-gap question).

Opening gap vs the 0DTE straddle, SPX only:
  overnight straddle = TODAY's expiry, at-the-money straddle, at YESTERDAY's last pull <= 16:00
                       (today's expiry is the second listed expiry in yesterday's raw file)
  AM straddle        = the same straddle at the first pull >= 09:30 with two-sided ATM quotes
  prior close        = SPX spot in yesterday's last pull <= 16:00
At the money = the strike where call mid and put mid are closest (put-call parity), so the
09:30 pull - whose index spot still carries the prior close - can't pick the wrong strike.
IV = the single Black-76 vol that prices call + put at that strike, forward from parity,
calendar-minute clock to the 16:15 settlement print (same clock as cvx_live / the page).

Shared by the live writer (cvx_live.py -> live.json "GAP") and the nightly replay
(cvx_replay.py). Pure functions plus one file scan; no vendor calls. The caller passes
the raw-record decoder and the expiry-date helper so this module has no imports of its own.
"""
import datetime
import glob
import gzip
import json
import math
import os
import re

SYM = "SPX"
EXP_MIN = 16 * 60 + 15          # SPX 0DTE prices to the 16:15 settlement print
_TS = re.compile(r'"ts"\s*:\s*"([^"]+)"')


def _mid(d):
    b, a = d.get("bid_price"), d.get("ask_price")
    try:
        b, a = float(b), float(a)
    except (TypeError, ValueError):
        return None
    if b <= 0 or a <= 0 or a < b:
        return None                      # one-sided or crossed: not a usable quote
    return (a + b) / 2.0


def _ncdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _straddle_px(F, K, T, sig):
    sq = sig * math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * sig * sig * T) / sq
    d2 = d1 - sq
    c = F * _ncdf(d1) - K * _ncdf(d2)
    p = K * _ncdf(-d2) - F * _ncdf(-d1)
    return c + p


def straddle_iv(px, F, K, T):
    """The one vol that prices call + put at K. None when it can't be inverted."""
    if not px or px <= 0 or T <= 0 or F <= 0 or K <= 0:
        return None
    if px <= abs(F - K) + 1e-9:
        return None
    lo, hi = 1e-4, 5.0
    for _ in range(80):
        m = (lo + hi) / 2.0
        if _straddle_px(F, K, T, m) < px:
            lo = m
        else:
            hi = m
    v = (lo + hi) / 2.0
    return v if 1e-3 < v < 4.9 else None


def years_to_settle(ts_iso, exp_iso):
    """Calendar minutes from the pull time to 16:15 ET on the expiry date, in years."""
    t = datetime.datetime.fromisoformat(str(ts_iso)[:19])
    e = datetime.datetime.fromisoformat(exp_iso + "T16:15:00")
    return max((e - t).total_seconds() / 60.0, 1.0) / 525600.0


def atm_straddle(book, ts_iso, exp_iso, near=None, band=150.0):
    """book {k: (call, put)} -> {k, c, p, straddle, F, iv} or None."""
    best = None
    for k, (c, p) in book.items():
        if near and abs(k - near) > band:
            continue
        mc, mp = _mid(c), _mid(p)
        if mc is None or mp is None:
            continue
        gap = abs(mc - mp)
        if best is None or gap < best[0]:
            best = (gap, k, mc, mp)
    if not best:
        return None
    _, k, mc, mp = best
    F = k + mc - mp
    T = years_to_settle(ts_iso, exp_iso)
    iv = straddle_iv(mc + mp, F, k, T)
    return {"t": str(ts_iso)[11:16], "k": k, "c": round(mc, 2), "p": round(mp, 2),
            "straddle": round(mc + mp, 2), "F": round(F, 2), "iv": round(iv, 4) if iv else None}


def _book_for(dec, exp_iso, exp_date):
    d = dec.get(SYM)
    if not d:
        return None, None
    for head, book in d.get("exps") or []:
        if exp_date(head) == exp_iso:
            return d.get("spot"), book
    return d.get("spot"), None


def am_read(dec, day_iso, ts_iso, exp_date):
    """AM straddle from one decoded pull (call it on pulls at/after 09:30 until it returns)."""
    if str(ts_iso)[11:16] < "09:30":
        return None
    spot, book = _book_for(dec, day_iso, exp_date)
    if not book:
        return None
    return atm_straddle(book, ts_iso, day_iso, near=spot)


def prev_raw(data_dir, day_ymd):
    """Newest raw_YYYYMMDD.ndjson.gz strictly before day_ymd (skips weekends/holidays)."""
    best = None
    for p in glob.glob(os.path.join(data_dir, "raw_*.ndjson.gz")):
        d = os.path.basename(p)[4:12]
        if d.isdigit() and d < day_ymd and (best is None or d > best[0]):
            best = (d, p)
    return best


def overnight_read(data_dir, day_iso, decode, exp_date, upto="16:00"):
    """Yesterday's last pull <= 16:00: prior close + today's-expiry ATM straddle.
    Scans one gz file (one pass, keeps one line). Returns a dict, or {"err": why}."""
    ymd = day_iso.replace("-", "")
    pr = prev_raw(data_dir, ymd)
    if not pr:
        return {"err": "no earlier raw file"}
    pday, path = pr
    pday_iso = "%s-%s-%s" % (pday[:4], pday[4:6], pday[6:8])
    last = None
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            head = line[:400]
            if '"pull": "idx"' not in head and '"pull":"idx"' not in head:
                continue
            m = _TS.search(head)
            ts = m.group(1) if m else None
            if ts is None:
                try:
                    ts = str(json.loads(line).get("ts", ""))
                except Exception:
                    continue
            if ts[:10] != pday_iso or ts[11:16] > upto:
                continue
            last = (ts, line)
    if not last:
        return {"err": "no pull <= %s in %s" % (upto, os.path.basename(path)), "prev_day": pday_iso}
    ts, line = last
    dec = decode(json.loads(line))
    spot, book = _book_for(dec, day_iso, exp_date)
    out = {"prev_day": pday_iso, "prev_t": ts[11:16], "prev_close": round(spot, 2) if spot else None}
    if not book:
        out["err"] = "today's expiry not listed in %s" % os.path.basename(path)
        return out
    on = atm_straddle(book, ts, day_iso, near=spot)
    if not on:
        out["err"] = "no two-sided ATM quote at %s" % ts[11:16]
        return out
    out["on"] = on
    return out


def block(on_part, am_part, day_iso):
    """The live.json "GAP" block / the replay's Open block inputs."""
    g = {"day": day_iso}
    if on_part:
        for k in ("prev_day", "prev_t", "prev_close", "on", "err"):
            if on_part.get(k) is not None:
                g[k] = on_part[k]
    if am_part:
        g["am"] = am_part
    return g


def summarize(g, open_px, close_px=None, high=None, low=None):
    """Numbers for the scorecard / history: gap, % of the overnight straddle, AM / overnight, used."""
    on, am, pc = (g or {}).get("on"), (g or {}).get("am"), (g or {}).get("prev_close")
    s = {"prev_close": pc, "open": open_px,
         "on_straddle": on and on["straddle"], "on_iv": on and on.get("iv"),
         "am_straddle": am and am["straddle"], "am_iv": am and am.get("iv")}
    if pc and open_px:
        s["gap"] = round(open_px - pc, 2)
        if on and on["straddle"]:
            s["gap_pct_on"] = round(100.0 * abs(open_px - pc) / on["straddle"], 1)
    if on and am and on["straddle"]:
        s["am_on_ratio"] = round(am["straddle"] / on["straddle"], 3)
        if on.get("iv") and am.get("iv"):
            s["iv_chg"] = round((am["iv"] - on["iv"]) * 100, 2)        # vol points
    if am and am["straddle"] and open_px:
        if close_px is not None:
            s["used_pct"] = round(100.0 * abs(close_px - open_px) / am["straddle"], 1)
        if high is not None and low is not None:
            s["range_pct"] = round(100.0 * (high - low) / am["straddle"], 1)
    return s


def gap_bucket(pct):
    if pct is None:
        return "no data"
    if pct < 25:
        return "small (<25% of overnight straddle)"
    if pct <= 75:
        return "medium (25–75%)"
    return "large (>75%)"
