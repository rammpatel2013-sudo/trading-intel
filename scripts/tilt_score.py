"""
tilt_score.py - created 2026-11-09 for Mithil. One-off test, no cockpit change.

QUESTION
  The Premium tab's "The tilt" (bullish minus bearish premium) disagrees between Side settings: with Quote it
  can read bullish while SPX falls, with Trade tag bearish. Which setting actually tracks price?

HOW (run INSIDE the cockpit, not a re-written copy of its maths)
  For every stored day, the production page cvx_cockpit.html is loaded headless (Chromium) with that day's
  minute log, exactly as the nightly replay does. For each Side setting - Quote, Trade tag, Per trade (only
  days with a sides_YYYYMMDD.json) - the page's OWN premDayData() gives the running tilt, net delta and SPX
  minute by minute. Then, on 15-minute windows from 09:45 to 16:00:
     1. Moves with price   - does the tilt rise in the same 15 minutes SPX rises?
     2. Moves before price - does a 15-minute tilt change call the NEXT 15 / 30 minutes of SPX?
     3. Day check          - does the tilt's sign at 16:00 match the day's direction (16:00 vs 09:30)?
     4. Down days only     - the same, on days SPX fell (where the split was seen).
  50% = coin flip. The same is shown for net delta (the yellow dashed line).

RUN (NAS, after the tarball pull - no image rebuild):
  sudo bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_script.sh tilt_score
  options: --days 20261028 20261029 ...   --no-telegram   --fresh (ignore the per-day cache)
  Each day's page read is cached in reports/tilt_score/days/, so a nightly run only replays the new day.
  Trade tag is scored only on days whose minute log carries trade tags (from 2026-11-05); Per trade only on
  days with a sides file. Before that the page falls back to the Quote rule, so the two would be identical.
OUT
  reports/tilt_score/tilt_score.html + tilt_score.json, verdict lines on stdout, one Telegram message + the HTML.
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent            # .../scripts
sys.path.insert(0, str(HERE))
import cvx_replay as R                            # noqa: E402  (load_flow, build_live, HOOK, CHROME_ARGS, telegram)

DATA = HERE / "cvxmap" / "data"
PAGE = HERE / "cvxmap" / "cvx_cockpit.html"
OUT = HERE.parent / "reports" / "tilt_score"
SIDES = (("cockpit", "Quote"), ("convex", "Trade tag"), ("trade", "Per trade"))
WIN = 15
HOOK_EXTRA = ("window.__cockpit={st,readBook,series,bursts,render,docGrades,gradeScenario,docPath};"
              "window.__tilt={st,series,signedRows,premDayData,pollSides,pollFlow,pollLive};")


def log(msg):
    print("%s tilt_score  %s" % (datetime.datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def tmin(t):
    h, m = str(t).split(":")[:2]
    return int(h) * 60 + int(m)


# ------------------------------------------------------------------ stage 1: the page, headless
def page_series(day_iso, flow_path, page_html):
    """{side: {"t":[min], "net":[$], "nd":[sh], "px":[spx], "uns": share}} from the production page itself."""
    from playwright.sync_api import sync_playwright
    flow, lines = R.load_flow(str(flow_path), day_iso)
    if "SPX" not in flow or len(flow["SPX"]) < 60:
        return None
    # Trade tags reach the minute log only from 2026-11-05 (Phase 2 fields cvb/cvs/pvb/pvs). Before that the page
    # falls back to the Quote rule for "Trade tag", so those days would compare Quote with itself: skip them.
    has_tag = any(any(v is not None for v in (j.get("cvb") or [])) for j in flow["SPX"].values())
    ks = sorted({k for j in flow["SPX"].values() for k in j.get("k", [])})
    oi = {"SPX": {"oi": {str(k): (0, 0) for k in ks}}}      # OI does not enter the tilt
    live = json.dumps(R.build_live(day_iso, R.T_END, flow, oi, None, None), separators=(",", ":")).encode()
    flow_b = ("\n".join(l for (t, s, l) in lines if s == "SPX") + "\n").encode()
    flow_name = "flow_%s.ndjson" % day_iso.replace("-", "")
    html = page_html.replace(R.HOOK, HOOK_EXTRA)
    if HOOK_EXTRA not in html:
        raise RuntimeError("page debug handle not found - the page changed; update HOOK_EXTRA")

    def serve(route):
        name = route.request.url.split("?")[0].rsplit("/", 1)[-1]
        if name == "cvx_cockpit.html":
            return route.fulfill(status=200, body=html, headers={"content-type": "text/html; charset=utf-8"})
        if name == "live.json":
            return route.fulfill(status=200, body=live, headers={"content-type": "application/json"})
        if name == flow_name:
            return route.fulfill(status=200, body=flow_b, headers={"content-type": "application/x-ndjson"})
        p = DATA / name
        if name.startswith("sides_") and p.is_file():
            return route.fulfill(status=200, body=p.read_bytes(), headers={"content-type": "application/json"})
        return route.fulfill(status=404, body=b"")

    out, errs = {}, []
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=R.CHROME_ARGS)
        ctx = b.new_context(viewport={"width": 1400, "height": 900})
        ctx.add_init_script("(() => { try{ localStorage.setItem('cockpit.sym','SPX'); localStorage.setItem('cockpit.premDayU','usd'); }catch(e){} })();")
        ctx.route("http://tilt.local/**", serve)
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: errs.append(str(e)[:160]))
        pg.goto("http://tilt.local/cvx_cockpit.html", wait_until="load")
        pg.wait_for_function("() => window.__tilt && window.__tilt.st && window.__tilt.st.live && (window.__tilt.st.flow.SPX||[]).length > 10", timeout=90000)
        has_sides = (DATA / ("sides_%s.json" % day_iso.replace("-", ""))).is_file()
        if has_sides:
            pg.evaluate("async () => { await window.__tilt.pollSides(); }")
        for key, _name in SIDES:
            if (key == "trade" and not has_sides) or (key == "convex" and not has_tag):
                continue
            res = pg.evaluate("""(k) => { const T=window.__tilt; T.st.sideSrc=k; T.st.premDayU='usd';
                const se=T.series('SPX'); if(!se) return null; const sr=T.signedRows(se); const D=T.premDayData(se); if(!D) return null;
                return {t:D.P.t, net:D.P.net, nd:D.P.nd, px:D.P.px, uns: sr? sr.unsShare : null}; }""", key)
            if res:
                out[key] = res
        b.close()
    if errs:
        log("%s page errors: %s" % (day_iso, "; ".join(errs[:3])))
    return out


# ------------------------------------------------------------------ stage 2: scoring
def at(series_t, series_v, m):
    """value of the series at minute m (last value at or before m)."""
    v = None
    for t, x in zip(series_t, series_v):
        if t > m:
            break
        if x is not None:
            v = x
    return v


def sgn(x, eps=0.0):
    return 0 if x is None or abs(x) <= eps else (1 if x > 0 else -1)


def windows(S, field):
    """[(d_tilt, d_spx_same, d_spx_next15, d_spx_next30)] per 15-min window 09:45..16:00 (anchored at 09:30)."""
    t, v, px = S["t"], S[field], S["px"]
    out = []
    for a in range(570, 960, WIN):
        b = a + WIN
        va, vb = at(t, v, a), at(t, v, b)
        pa, pb = at(t, px, a), at(t, px, b)
        pn, pn2 = at(t, px, min(b + WIN, 960)), at(t, px, min(b + 2 * WIN, 960))
        if None in (va, vb, pa, pb):
            continue
        out.append((vb - va, pb - pa, (pn - pb) if (pn is not None and b < 960) else None,
                    (pn2 - pb) if (pn2 is not None and b < 960) else None))
    return out


def agree(pairs):
    """share of pairs with the same non-zero sign, and the count."""
    hits = n = 0
    for x, y in pairs:
        sx, sy = sgn(x), sgn(y)
        if sx == 0 or sy == 0:
            continue
        n += 1
        hits += sx == sy
    return (hits / n if n else None), n


def pval(share, n):
    """two-sided p that a coin flip gives a share this far from 50% (normal approx)."""
    if not n or share is None:
        return None
    z = abs(share - 0.5) / math.sqrt(0.25 / n)
    return math.erfc(z / math.sqrt(2))


def score(days):
    """days = {day_iso: {side: S}} -> per side, per field results."""
    res = {}
    for key, name in SIDES:
        for field in ("net", "nd"):
            same, lead15, lead30, dsame, dl15 = [], [], [], [], []
            dayck, rows = [], []
            for d, sides in sorted(days.items()):
                S = sides.get(key)
                if not S:
                    continue
                w = windows(S, field)
                p0, p1 = at(S["t"], S["px"], 570) or at(S["t"], S["px"], 571), at(S["t"], S["px"], 960)
                fin = at(S["t"], S[field], 960)
                down = p0 is not None and p1 is not None and p1 < p0
                same += [(a, b) for a, b, _c, _e in w]
                lead15 += [(a, c) for a, _b, c, _e in w if c is not None]
                lead30 += [(a, e) for a, _b, _c, e in w if e is not None]
                if down:
                    dsame += [(a, b) for a, b, _c, _e in w]
                    dl15 += [(a, c) for a, _b, c, _e in w if c is not None]
                if p0 is not None and p1 is not None and fin is not None:
                    dayck.append((fin, p1 - p0))
                    rows.append({"day": d, "spx_pct": round(100 * (p1 / p0 - 1), 2), "final": fin,
                                 "match": sgn(fin) == sgn(p1 - p0), "uns": S.get("uns")})
            r = {"days": len(rows), "rows": rows}
            for nm, pairs in (("same", same), ("lead15", lead15), ("lead30", lead30), ("down_same", dsame), ("down_lead15", dl15), ("day", dayck)):
                sh, n = agree(pairs)
                r[nm] = {"share": sh, "n": n, "p": pval(sh, n)}
            res["%s|%s" % (key, field)] = r
    return res


def pct(x):
    return "--" if x is None else "%d%%" % round(100 * x)


def verdict(name, r):
    s, l1, l3, dy, ds = r["same"], r["lead15"], r["lead30"], r["day"], r["down_same"]
    if not r["days"]:
        return "%s: no days to test." % name
    def tag(x):
        if x["share"] is None or x["n"] < 20:
            return "too few"
        if x["p"] is not None and x["p"] < 0.05:
            return "real" if x["share"] > 0.5 else "real, OPPOSITE"
        return "coin flip"
    return ("%s (%d days): moved WITH price %s of %d windows (%s); called the NEXT 15 min %s (%s), next 30 min %s (%s); "
            "close sign matched the day %d of %d; on down days moved with price %s (%s)."
            % (name, r["days"], pct(s["share"]), s["n"], tag(s), pct(l1["share"]), tag(l1), pct(l3["share"]), tag(l3),
               round((dy["share"] or 0) * dy["n"]), dy["n"], pct(ds["share"]), tag(ds)))


def html_report(res, days):
    css = ("body{background:#0A1426;color:#E8EFF8;font:16px/1.55 'IBM Plex Sans Condensed',system-ui,sans-serif;margin:0;padding:18px;max-width:1100px}"
           "h1{font-size:24px}h2{font-size:19px;margin-top:26px}table{border-collapse:collapse;margin:8px 0;width:100%}"
           "td,th{border-bottom:1px solid #1B2C4C;padding:6px 8px;text-align:right}th{color:#B9C8DC;text-align:right}td:first-child,th:first-child{text-align:left}"
           ".ok{color:#58D68D;font-weight:700}.bad{color:#FF7B72;font-weight:700}.mute{color:#B9C8DC}.box{background:#0E1B33;border-radius:8px;padding:10px 14px;margin:10px 0}")
    h = ["<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
         "<title>Tilt vs price</title><style>%s</style></head><body>" % css,
         "<h1>Premium tab tilt: which Side setting tracks price?</h1>",
         "<div class='box'>Each stored day was replayed in the cockpit itself. For each Side setting the page's own tilt "
         "(bullish minus bearish premium, the white line) and net delta (yellow dashed) were read every minute and compared "
         "with SPX on 15-minute windows. <b>50%% = coin flip.</b> \"real\" means unlikely to be luck (p &lt; 0.05). "
         "Days tested: %d (%s to %s).</div>" % (len(days), min(days) if days else "-", max(days) if days else "-")]
    for field, title in (("net", "Tilt (bullish minus bearish $)"), ("nd", "Net delta (share-equivalents)")):
        h.append("<h2>%s</h2><table><tr><th>Side setting</th><th>days</th><th>moves with price</th><th>calls next 15 min</th>"
                 "<th>calls next 30 min</th><th>close sign = day</th><th>down days: with price</th></tr>" % title)
        for key, name in SIDES:
            r = res["%s|%s" % (key, field)]
            def cell(x):
                if x["share"] is None:
                    return "<td class='mute'>--</td>"
                cls = "ok" if (x["p"] is not None and x["p"] < 0.05 and x["share"] > 0.5) else ("bad" if (x["p"] is not None and x["p"] < 0.05) else "")
                return "<td class='%s'>%s <span class='mute'>(n %d)</span></td>" % (cls, pct(x["share"]), x["n"])
            h.append("<tr><td><b>%s</b></td><td>%d</td>%s%s%s%s%s</tr>" % (name, r["days"], cell(r["same"]), cell(r["lead15"]),
                                                                           cell(r["lead30"]), cell(r["day"]), cell(r["down_same"])))
        h.append("</table>")
    h.append("<h2>Day by day (tilt at 16:00)</h2><table><tr><th>day</th><th>SPX 09:30→16:00</th>"
             + "".join("<th>%s</th>" % n for _k, n in SIDES) + "</tr>")
    by = {}
    for key, _n in SIDES:
        for row in res["%s|net" % key]["rows"]:
            by.setdefault(row["day"], {})[key] = row
    for d in sorted(by):
        any_r = next(iter(by[d].values()))
        cells = []
        for key, _n in SIDES:
            r = by[d].get(key)
            if not r:
                cells.append("<td class='mute'>--</td>")
                continue
            v = r["final"]
            cells.append("<td class='%s'>%s$%.0fM%s</td>" % ("ok" if r["match"] else "bad", "+" if v >= 0 else "−", abs(v) / 1e6,
                                                           (" <span class='mute'>uns %d%%</span>" % round(100 * r["uns"])) if r.get("uns") is not None else ""))
        h.append("<tr><td>%s</td><td>%+.2f%%</td>%s</tr>" % (d, any_r["spx_pct"], "".join(cells)))
    h.append("</table><p class='mute'>Green = the tilt's sign at the close matched the day's direction, red = it did not. "
             "\"uns\" = share of premium that setting left unsigned. Descriptive test on a small sample; not a trade signal.</p></body></html>")
    return "".join(h)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", nargs="*")
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--fresh", action="store_true", help="replay every day again (ignore the per-day cache)")
    args = ap.parse_args()
    page = PAGE.read_text(encoding="utf-8")
    files = sorted(DATA.glob("flow_2*.ndjson"))
    want = set(args.days or [])
    days = {}
    for fp in files:
        ymd = fp.stem.split("_")[1]
        if want and ymd not in want:
            continue
        d = datetime.date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:]))
        if d.weekday() >= 5:
            continue
        cache = OUT / "days" / ("%s.json" % ymd)
        sp = DATA / ("sides_%s.json" % ymd)
        newest = max(fp.stat().st_mtime, sp.stat().st_mtime if sp.is_file() else 0)
        if not args.fresh and cache.is_file() and cache.stat().st_mtime > newest and d < datetime.date.today():
            S = json.loads(cache.read_text(encoding="utf-8"))
            if S:
                days[d.isoformat()] = S
            continue
        try:
            S = page_series(d.isoformat(), fp, page)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(S or {}), encoding="utf-8")
        except Exception as exc:
            log("%s skipped: %s" % (ymd, str(exc)[:160]))
            continue
        if S and S.get("cockpit") and len(S["cockpit"]["t"]) > 60:
            days[d.isoformat()] = S
            log("%s ok  sides: %s" % (ymd, ",".join(S)))
        else:
            log("%s skipped: not enough regular-hours minutes" % ymd)
    if not days:
        log("no days to score")
        return 1
    res = score(days)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "tilt_score.json").write_text(json.dumps({"days": sorted(days), "res": res}, indent=1, default=str), encoding="utf-8")
    (OUT / "tilt_score.html").write_text(html_report(res, days), encoding="utf-8")
    lines = ["Tilt vs price — %d days (%s → %s). 50%% = coin flip." % (len(days), min(days), max(days))]
    for key, name in SIDES:
        lines.append(verdict(name, res["%s|net" % key]))
    print("\n".join(lines), flush=True)
    if not args.no_telegram:
        R.telegram("\n\n".join(lines), [(str(OUT / "tilt_score.html"), "Tilt vs price - full table")])
    return 0


if __name__ == "__main__":
    sys.exit(main())
