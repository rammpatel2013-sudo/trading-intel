"""
cvx_replay.py - nightly replay + scorecard of the Convex (NAS) cockpit. Created 2026-10-28.

Runs Mithil's own Convex cockpit page (scripts/cvxmap/cvx_cockpit.html) headless in Chromium over
the finished session. The page clock is stepped from 09:31 to 16:00, so every number on the
scorecard is the page's own maths, not a re-implementation:
  * Density cone - the page's worker, 78 five-minute runs (20k paths each)
  * Map frames   - Gamma on the 0DTE book; Charm / Vanna / Change on the All book; basis OI + vol
  * Charm clock  - the levels_check.html functions, verbatim, run in Chromium
  * Seven locked tests (close-prediction-architecture section 9, v1 conventions) on the page's
    own 0DTE map, on all three bases (OI / OI + vol / vol)
  * A replay video of the day (Density, then the three map views), the scorecard HTML,
    reports/replay/history.csv, and a Telegram push

NAS Convex files only (ToS is not on the NAS):
  scripts/cvxmap/data/raw_YYYYMMDD.ndjson.gz  0DTE OI (first idx pull >= 09:31) + SPX bid/ask per minute
  scripts/cvxmap/data/flow_YYYYMMDD.ndjson    the minute log, t <= 16:00
  scripts/cvxmap/data/ti.json                 near_book (Convex 08:35 OI) for the All book
  scripts/cvxmap/cvx_cockpit.html             the page (repo copy = newest; data/ copy as fallback)

Run (NAS DSM task, Mon-Fri 22:15, user root):
  bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_script.sh cvx_replay
Options:
  --date YYYYMMDD   replay another day (default: today, ET)
  --data DIR        where raw/flow/ti live (default scripts/cvxmap/data)
  --out DIR         default reports/replay
  --step N          minutes between frames (default 5)
  --no-telegram     --no-video     --extract-only (stop after the raw extract; no browser)
"""
import argparse
import base64
import csv
import datetime
import gzip
import hashlib
import io
import json
import math
import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent            # .../scripts
CVX = HERE / "cvxmap"
SYMS = ("SPX", "SPY", "QQQ")
RTH_OPEN = 570
T_END = "16:00"
BASES = ("oi", "oiv", "vol")
VIEW_BASIS = "oiv"           # Mithil: gamma on 0DTE OI + vol; charm / vanna / change on All DTE
BASIS_NAME = {"oi": "OI", "oiv": "OI + vol", "vol": "volume"}
VIEWS = (("density", "Density cone"),
         ("charm", "Map: Gamma 0DTE | Charm All"),
         ("vanna", "Map: Gamma 0DTE | Vanna All"),
         ("chg", "Map: Gamma 0DTE | Change All"))

# The page's debug handle. The replay copy exposes a few more of the page's OWN functions so the
# harness can read them and poll on demand. No maths is changed; the live page is untouched.
HOOK = "window.__cockpit={st,readBook,series,bursts,render,docGrades,gradeScenario,docPath}"
REPLAY_HOOK = ("window.__cockpit={st,readBook,series,bursts,render,docGrades,gradeScenario,docPath,"
               "buildSeries,signedRows,buildMap,greekCol,greekColNear,nearRows,mapRowsAt,changeMap,"
               "currentBook,liveClock,pollLive,pollFlow,pollTI}")
# Docker gives /dev/shm 64 MB; Chromium must not rely on it inside the trading-intel container
CHROME_ARGS = ["--disable-dev-shm-usage"]
DEN_HOOK = "window.__density=DEN;"
DEN_REPLAY = "window.__density=DEN; DEN.kick=denTick;"

# levels_check.html charm clock, VERBATIM (source md5 b8c0919d2f5560cf66482face6602a7b, 2026-10-28).
# Only the localStorage history line is left out.
CHARM_JS = r"""const EXP_MIN = 975, HZN = 60;
let chNow = null, chFlips = [], chSpot = null, chHist = {};

function ncdf(x){ const t = 1/(1+0.2316419*Math.abs(x)), d = 0.3989422804014327*Math.exp(-x*x/2);
  const p = d*t*(0.319381530+t*(-0.356563782+t*(1.781477937+t*(-1.821255978+t*1.330274429)))); return x > 0 ? 1-p : p; }
function nd1(S, K, sig, tauMin){
  if (tauMin <= 0.5) return S > K ? 1 : (S < K ? 0 : 0.5);
  const v = sig*Math.sqrt(tauMin/525600); return ncdf((Math.log(S/K) + 0.5*v*v)/v);
}
function stkIV(r, S, atm){ const c = +r.cIV||0, p = +r.pIV||0; const o = (+r.k >= S) ? (c||p) : (p||c);
  return (o > 0.5*atm && o < 3*atm) ? o : atm; }   // bad RTD IVs (0, 0.05) fall back to ATM
function charmFlow(S, rp, nowMin, H){
  const t0 = EXP_MIN - nowMin, t1 = Math.max(t0 - H, 0); let f = 0;
  for (const r of rp) f += r.net * (nd1(S, r.k, r.sig, t1) - nd1(S, r.k, r.sig, t0));
  return -2 * f;
}
function charmFlips(S, rp, nowMin){
  const out = []; let prev = null;
  for (let X = Math.round(S) - 75; X <= Math.round(S) + 75; X += 1){
    const v = charmFlow(X, rp, nowMin, HZN);
    if (prev && prev.v !== 0 && v !== 0 && Math.sign(v) !== Math.sign(prev.v)){
      const p = prev.X + (X - prev.X)*Math.abs(prev.v)/(Math.abs(prev.v) + Math.abs(v));
      out.push({p, txt: prev.v > 0 ? "buy below / sell above" : "sell below / buy above"});
    }
    prev = {X, v};
  }
  return out;
}
"""

# How the clock is run per minute (same as the 10/28 cloud harness run_charm.js): ATM IV = mean of
# the ATM call/put IV in that minute's row; every strike's sigma via stkIV; net = cOI - pOI.
CHARM_RUN = r"""(args) => { const OI=args.oi, lines=args.rows, out=[];
  const mm=t=>{const [h,m]=t.split(':').map(Number);return h*60+m;};
  for(const j of lines){ const S=j.x; let q=0; j.k.forEach((k,i)=>{ if(Math.abs(k-S)<Math.abs(j.k[q]-S)) q=i; });
    const atmC=j.ci[q], atmP=j.pi[q]; const atm=(atmC>0&&atmP>0)?(atmC+atmP)/2:(atmC||atmP||0.15);
    const rows=j.k.map((k,i)=>{ const o=OI[String(Number(k).toFixed(1))]||[0,0]; return {k:+k, cOI:o[0], pOI:o[1], cIV:j.ci[i], pIV:j.pi[i]}; });
    const rp=rows.map(r=>({k:r.k, net:(r.cOI||0)-(r.pOI||0), sig:window.__cc.stkIV(r,S,atm)})).filter(r=>r.net&&r.k>0);
    const now=mm(j.t); const v=window.__cc.charmFlow(S,rp,now,window.__cc.HZN); const fl=window.__cc.charmFlips(S,rp,now);
    out.push({t:j.t,S,es:+v.toFixed(1),usd_m:+(Math.abs(v)*50*S/1e6).toFixed(0),flips:fl.map(f=>+f.p.toFixed(1)),ftxt:fl.map(f=>f.txt)}); }
  return out; }"""

# A run is due once its minute (or one of the next 3) is in the log; a run whose minute is missing
# (Convex gap) is computed or marked skipped by the page at a later step, as it would be live.
JS_DEN_STATE = r"""(last) => { const D=window.__density, st=window.__cockpit.st; if(!D) return {ok:false,why:'no density'};
  const tm=t=>{const [h,m]=t.split(':').map(Number);return h*60+m;};
  const snaps=new Set(((st.flow&&st.flow[st.sym])||[]).map(s=>tm(s.t))), now=570+last;
  const due=k=>{ const T=570+k; for(let t=Math.max(T,571);t<=T+3;t++) if(snaps.has(t)) return true; return false; };
  const have=k=>!!(D.runs[k]||D.runs['x'+k]); let miss=[]; for(let k=0;k<=last;k+=5) if(!have(k)&&due(k)) miss.push(k);
  return {ok:miss.length===0, miss:miss.length, busy:!!D.busy, tripped:!!D.tripped, err:D.err||null,
          n:Object.keys(D.runs).filter(k=>!k.startsWith('x')).length}; }"""

JS_DEN_OUT = r"""() => { const D=window.__density, st=window.__cockpit.st;
  const runs=Object.keys(D.runs).filter(k=>!k.startsWith('x')).map(Number).sort((a,b)=>a-b).map(k=>{ const r=D.runs[k];
    return {t:r.t,k:r.k,spot:r.spot,strad:r.strad,sd:r.sd,med:r.med,p10:r.p10,p90:r.p90,zone:r.zone,pZone:r.pZone,
            pAboveGz:r.pAboveGz,pRails:r.pRails,gz:r.gz,vt:r.vt,vb:r.vb}; });
  const skipped=Object.keys(D.runs).filter(k=>k.startsWith('x')).map(k=>+k.slice(1)).sort((a,b)=>a-b);
  return {runs, skipped, basis:st.basis, sym:st.sym, charm:D.charm||[], err:D.err||null, tripped:!!D.tripped}; }"""

JS_LEVELS = r"""(bases) => { const c=window.__cockpit, st=c.st, d=st.live[st.sym], se=c.series(st.sym), keep=st.basis, out={};
  try{ for(const b of bases){ st.basis=b;
      const g=c.buildMap('gamma',se,d,'0dte'), v=c.buildMap('vanna',se,d,'0dte'), ch=c.buildMap('charm',se,d,'0dte');
      if(!g||!v||!ch){ out[b]=null; continue; }
      const P=(m,f)=>m.prices[0]+f*m.dp; const cols=[];
      g.times.forEach((t,i)=>{ if(t<570||t>g.now) return;
        const gz=g.feats[i].z.map(f=>P(g,f)), vz=v.feats[i].z.map(f=>P(v,f)), cz=ch.feats[i].z.map(f=>P(ch,f));
        let gpk=null, gv=-1e99; g.feats[i].pk.forEach(k=>{ if(g.grid[i][k]>gv){ gv=g.grid[i][k]; gpk=g.prices[k]; } });
        cols.push({t,gz,vz,cz,gpk}); });
      out[b]={cols, now:g.now}; } }
  finally{ st.basis=keep; }
  return out; }"""

JS_VIEW = r"""(v) => { const c=window.__cockpit, st=c.st;
  if(v==='density'){ st.tab='density'; } else { st.tab='map'; st.mapBook='0dte'; st.mapBookR='all'; st.mapRight=v; }
  c.render(); return {tab:st.tab, right:st.mapRight, book:st.mapBook, bookR:st.mapBookR, basis:st.basis, weekSrc:st.weekSrc}; }"""

JS_ADVANCE = r"""async () => { const c=window.__cockpit; await c.pollLive(); await c.pollFlow();
  try{ window.__density && window.__density.kick && window.__density.kick(); }catch(e){}
  const st=c.st, f=(st.flow&&st.flow[st.sym])||[]; return {snaps:f.length, last:f.length?f[f.length-1].t:null, ts:st.live&&st.live.ts}; }"""

CONV = {
    "levels": "the page's buildMap('gamma'/'vanna','0dte') per 2-min column, column at/before the minute; gz = gamma zero nearest spot; vanna top/bottom only when the column has exactly 2 vanna zeros (as Density draws them); gamma peak = largest gamma local max",
    "spot": "Convex minute log x (NAS flow file); a missing minute (Convex gap) uses the nearest earlier minute within 3",
    "open": "a 09:30 entry fills at 09:31: the 09:30 row and quotes are pre-open",
    "fills": "Convex raw bid/ask at that minute (nearest earlier minute within 3 if missing); buy at ask, sell at bid, both ways; x100 per contract",
    "fly": "long fly, calls if centre >= spot else puts, strikes rounded to 5; trigger = spot within 5 pts of any transition 10:00-13:00, 60-min cooldown; width 25 before 11:00, else 20",
    "fly_exit": "profit target 25% (middle of the 20-30% band) on the debit; stop when the fly is worth <= 50% of the debit; else close at 15:30",
    "stretch": "|spot - gamma zero| >= 1 SD, SD = 0DTE ATM straddle / 0.7979",
    "credit": "one iron condor per entry (09:31, 10:00, 10:30, 11:00, 11:30) at ceil5(vanna top+20)/+25 and floor5(vanna bottom-20)/-25; target 50%; stop = buy back at 2x the credit; time exit 14:00",
    "debit": "entries 09:31, 10:00, 10:30; long call = ceil5(gamma peak + 20), 25 wide; exit at 4x debit else 15:00",
    "oldfly": "short iron fly 25 wide on r5(gamma zero), entries 09:31 then every 30 min to 13:00; credit >= $1,800 else no trade; buy back at <= $1,000, else 14:00",
}
TEST_NAMES = ["1 Fly @ vanna top", "2 Fly @ gamma zero", "3 Fly @ vanna bottom", "4 Fly @ gamma zero +1SD",
              "5 Credit spread", "6 Debit 1:5", "7 Old fly"]
# Starting point of the running total: Mon 10/26 + Tue 10/27, scored on the ToS minute log, VOLUME basis,
# 1 contract per signal, $50k book (the table Mithil keeps). Per test: (n, risk $, P/L $).
SEED = {"since": "2026-10-26", "basis": "vol", "label": "Mon 10/26 + Tue 10/27 (ToS feed, volume basis)",
        "tests": {"1 Fly @ vanna top": (7, 4025, 1105), "2 Fly @ gamma zero": (7, 3825, 165),
                  "3 Fly @ vanna bottom": (7, 1610, -905), "4 Fly @ gamma zero +1SD": (3, 985, 295),
                  "5 Credit spread": (5, 11630, 455), "6 Debit 1:5": (5, 320, 690), "7 Old fly": (8, 3800, -830)}}
BOOK = 50000.0


# ------------------------------------------------------------------ helpers
def log(msg):
    print("%s  %s" % (et_now().strftime("%H:%M:%S"), msg), flush=True)


def et_now():
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo("America/New_York")).replace(tzinfo=None)
    except Exception:
        u = datetime.datetime.utcnow()
        y = u.year
        mar1 = datetime.datetime(y, 3, 1)
        dst0 = mar1 + datetime.timedelta(days=(6 - mar1.weekday()) % 7 + 7, hours=7)   # 2nd Sun Mar 07:00 UTC
        nov1 = datetime.datetime(y, 11, 1)
        dst1 = nov1 + datetime.timedelta(days=(6 - nov1.weekday()) % 7, hours=6)       # 1st Sun Nov 06:00 UTC
        return u - datetime.timedelta(hours=4 if dst0 <= u < dst1 else 5)


def mm(t):
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def hh(m):
    return "%02d:%02d" % (m // 60, m % 60)


def md5_bytes(b):
    return hashlib.md5(b).hexdigest()


def md5_file(p):
    h = hashlib.md5()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _n(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else 0.0
    except Exception:
        return 0.0


# ------------------------------------------------------------------ stage 1: raw extract
def extract(raw_path, day):
    """0DTE OI (first idx pull >= 09:31), the week book from that pull, SPX 0DTE bid/ask per minute."""
    sys.path.insert(0, str(CVX))
    import cvx_live   # the writer's own decoder and week builder
    day_iso = day.isoformat()
    oi, week, quotes, spot = {}, {}, {}, {}
    oi_ts, n_idx, exp_bad = None, 0, []
    with gzip.open(raw_path, "rt", encoding="utf-8") as fh:
        for line in fh:
            head = line[:400]
            if '"pull": "idx"' not in head and '"pull":"idx"' not in head:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            ts = str(rec.get("ts", ""))
            if ts[:10] != day_iso:
                continue
            t = ts[11:16]
            if t < "09:30" or t > T_END:
                continue
            try:
                dec = cvx_live.decode(rec)
            except Exception:
                continue
            n_idx += 1
            if oi_ts is None and t >= "09:31":
                for sym in SYMS:
                    d = dec.get(sym)
                    if not d or not d["exps"]:
                        continue
                    head0, book0 = d["exps"][0]
                    e = cvx_live.exp_date(head0)
                    if e != day_iso:
                        exp_bad.append("%s %s" % (sym, e))
                    oi[sym] = {"exp": e, "ts": ts,
                               "oi": {"%.1f" % k: [_n(c.get("oi")), _n(p.get("oi"))] for k, (c, p) in book0.items()}}
                try:
                    week = cvx_live.build_week(dec, day)
                except Exception as exc:
                    log("week book failed: %s" % exc)
                oi_ts = ts
            d = dec.get("SPX")
            if d and d["exps"]:
                head0, book0 = d["exps"][0]
                if cvx_live.exp_date(head0) == day_iso:
                    S = d["spot"]
                    quotes[t] = {str(int(k)): [c.get("bid_price"), c.get("ask_price"), p.get("bid_price"), p.get("ask_price")]
                                 for k, (c, p) in book0.items() if abs(k - S) <= 200}
                    spot[t] = S
    return {"day": day_iso, "oi": oi, "oi_ts": oi_ts, "week": week, "q": quotes, "spot": spot,
            "n_idx": n_idx, "exp_bad": exp_bad}


# ------------------------------------------------------------------ stage 2: inputs for the page
def load_flow(path, day_iso):
    per, lines = {}, []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                j = json.loads(line)
            except Exception:
                continue
            if j.get("d") != day_iso or j.get("t", "99") > T_END or j.get("s") not in SYMS:
                continue
            per.setdefault(j["s"], {})[j["t"]] = j
    for s, d in per.items():
        for t, j in d.items():
            lines.append((t, s, json.dumps(j, separators=(",", ":"))))
    lines.sort()
    return per, lines


def build_live(day_iso, T, flow, oi, tmpl, week):
    """live.json as the writer would have had it at minute T: rows carry the day's 0DTE OI, the IV /
    volume / quotes of the last logged minute <= T; ts sets the page clock."""
    live = {"ts": "%sT%s:00" % (day_iso, T)}
    for s in SYMS:
        snaps = {t: j for t, j in (flow.get(s) or {}).items() if t <= T}
        if not snaps or s not in oi:
            continue
        last = snaps[max(snaps)]
        opens = [t for t in snaps if t >= "09:31"]
        first = snaps[min(opens)] if opens else last
        idx = {k: i for i, k in enumerate(last["k"])}
        rows = []
        for ks, (coi, poi) in sorted(oi[s]["oi"].items(), key=lambda a: float(a[0])):
            k = float(ks)
            k = int(k) if k == int(k) else k
            i = idx.get(k)

            def g(f, i=i):
                return ((last[f][i] if (i is not None and last.get(f)) else 0) or 0)
            rows.append({"k": k, "cOI": coi, "pOI": poi, "cV": g("cv"), "pV": g("pv"), "cIV": g("ci"), "pIV": g("pi"),
                         "cM": g("cm"), "pM": g("pm"), "cL": g("cl"), "pL": g("pl"), "cB": g("cb"), "cA": g("ca"),
                         "cZ": g("cz"), "pB": g("pb"), "pA": g("pa"), "pZ": g("pz"), "cD": g("cd"), "pD": g("pd")})
        lk = last["k"]
        q = min(range(len(lk)), key=lambda i: abs(lk[i] - last["x"]))
        b = (tmpl.get(s) or {}) if tmpl else {}
        live[s] = {"spot": last["x"], "open": first["x"], "pct": (last["x"] / first["x"] - 1.0) if first["x"] else 0,
                   "iv": ((last["ci"][q] or 0) + (last["pi"][q] or 0)) / 2, "iv_open": 0,
                   "straddle": (last["cm"][q] or 0) + (last["pm"][q] or 0), "exp": day_iso,
                   "step": b.get("step", 5 if s == "SPX" else 1), "rows": rows,
                   "tl": [[t, snaps[t]["x"]] for t in sorted(snaps) if t >= "09:30"], "ser": [],
                   "term": b.get("term", []), "hv": b.get("hv", {"hv20": 0, "sqlog20": 0, "hv252": 0})}
    vx = dict((tmpl or {}).get("VIX") or {"spot": 0})
    vx["v9d"] = 0          # as the writer serves it (V9D_INTO_MAP = False): the week re-mark uses its ATM fallback
    live["VIX"] = vx
    live["ES"] = {"last": 0, "cvd": 0, "tl": []}
    live["week"] = week or {}
    live["_source"] = "convex-replay"
    return live


# ------------------------------------------------------------------ stage 3: the page, headless
def replay_page(args, day_iso, page_html, flow, flow_lines, oi, tmpl, week, ti_bytes, week_src, data_dir, out_dir, step):
    from playwright.sync_api import sync_playwright
    flow_name = "flow_%s.ndjson" % day_iso.replace("-", "")
    state = {"live": b"{}", "flow": b""}
    times = ["09:31"] + [hh(m) for m in range(RTH_OPEN + step, mm(T_END) + 1, step)]
    if times[-1] != T_END:
        times.append(T_END)
    frames = {v: [] for v, _ in VIEWS}
    fdir = out_dir / "frames"
    fdir.mkdir(parents=True, exist_ok=True)
    res = {"times": times, "frames": frames, "console": []}

    def serve(route):
        url = route.request.url.split("?")[0]
        name = url.rsplit("/", 1)[-1]
        if name == "cvx_cockpit.html":
            return route.fulfill(status=200, body=page_html, headers={"content-type": "text/html; charset=utf-8"})
        if name == "live.json":
            return route.fulfill(status=200, body=state["live"], headers={"content-type": "application/json"})
        if name == flow_name:
            return route.fulfill(status=200, body=state["flow"], headers={"content-type": "application/x-ndjson"})
        if name == "ti.json":
            if ti_bytes:
                return route.fulfill(status=200, body=ti_bytes, headers={"content-type": "application/json"})
            return route.fulfill(status=404, body=b"")
        p = data_dir / name
        if name.endswith((".json", ".ndjson")) and not name.startswith("raw_") and p.is_file() and p.stat().st_size < 60e6:
            return route.fulfill(status=200, body=p.read_bytes())
        return route.fulfill(status=404, body=b"")

    def set_time(T):
        state["live"] = json.dumps(build_live(day_iso, T, flow, oi, tmpl, week), separators=(",", ":")).encode()
        state["flow"] = ("\n".join(l for (t, s, l) in flow_lines if t <= T) + "\n").encode()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=CHROME_ARGS)
        ctx = browser.new_context(viewport={"width": 1600, "height": 1000}, device_scale_factor=1)
        prefs = {"basis": VIEW_BASIS, "mapBook": "0dte", "mapBookR": "all", "mapRight": "charm",
                 "weekSrc": week_src, "denOn": "1", "sym": "SPX"}
        ctx.add_init_script("(p)=>{}")  # placeholder so the next script runs first on every page
        ctx.add_init_script("""(() => { const p=%s; try{ for(const k in p) localStorage.setItem('cockpit.'+k, p[k]); }catch(e){} })();"""
                            % json.dumps(prefs))
        ctx.route("http://replay.local/**", serve)
        page = ctx.new_page()
        page.on("pageerror", lambda e: res["console"].append("pageerror: %s" % str(e)[:200]))
        page.on("console", lambda m: res["console"].append("%s: %s" % (m.type, m.text[:200])) if m.type in ("error", "warning") else None)
        set_time(times[0])
        page.goto("http://replay.local/cvx_cockpit.html", wait_until="load")
        page.wait_for_function("() => window.__cockpit && window.__cockpit.st && window.__cockpit.st.live && window.__density", timeout=60000)
        res["hook_ok"] = page.evaluate("() => typeof window.__cockpit.pollFlow === 'function' && typeof window.__density.kick === 'function'")
        # the freshness line compares the REAL clock with the replayed ts ("last wrote 400 min ago"): swap it for a replay badge
        page.add_style_tag(content=".fresh{visibility:hidden}#replayBadge{position:fixed;top:14px;right:16px;z-index:99999;"
                                   "background:#7c3aed;color:#fff;font:700 15px/1.6 system-ui,sans-serif;padding:3px 12px;border-radius:6px}")
        page.evaluate("(t)=>{ const b=document.createElement('div'); b.id='replayBadge'; b.textContent=t; document.body.appendChild(b); }",
                      "REPLAY · Convex (NAS) · " + datetime.date.fromisoformat(day_iso).strftime("%a %d %b %Y"))
        t_start = time.time()
        t_den = t_shot = 0.0
        for n, T in enumerate(times):
            set_time(T)
            adv = page.evaluate(JS_ADVANCE)
            last = min(385, ((mm(T) - RTH_OPEN) // 5) * 5)
            t0 = time.time()
            stt = None
            while time.time() - t0 < 240:
                stt = page.evaluate(JS_DEN_STATE, last)
                if stt.get("ok") or stt.get("tripped"):
                    break
                time.sleep(0.4)
            t1 = time.time()
            t_den += t1 - t0
            if not args.no_video or T == times[-1]:
                for v, _ in VIEWS:
                    page.evaluate(JS_VIEW, v)
                    page.wait_for_timeout(350)
                    fp = fdir / ("%s_%03d.jpg" % (v, n))
                    page.screenshot(path=str(fp), type="jpeg", quality=82)
                    frames[v].append((T, fp))
            t_shot += time.time() - t1
            if n % 6 == 0 or T == times[-1]:
                log("frame %s  snaps %s  density %s/%s%s  %.0fs (density waits %.0fs, views %.0fs)" % (T, adv.get("snaps"), stt and stt.get("n"),
                    len(range(0, last + 1, 5)), " TRIPPED" if stt and stt.get("tripped") else "", time.time() - t_start, t_den, t_shot))
        res["view_state"] = page.evaluate(JS_VIEW, "charm")
        res["density"] = page.evaluate(JS_DEN_OUT)
        res["levels"] = page.evaluate(JS_LEVELS, list(BASES))
        # charm clock: a blank page, the functions verbatim, the day's minute rows + 09:31 OI
        p2 = ctx.new_page()
        p2.set_content("<html><body></body></html>")
        p2.add_script_tag(content="(function(){\n" + CHARM_JS +
                          "\nwindow.__cc={charmFlow,charmFlips,stkIV,HZN,EXP_MIN};\n})();")
        rows = [flow["SPX"][t] for t in sorted(flow.get("SPX", {})) if "09:31" <= t <= "15:59"]
        res["charm"] = p2.evaluate(CHARM_RUN, {"oi": oi["SPX"]["oi"], "rows": rows})
        res["cards"] = []
        res["browser_s"] = round(time.time() - t_start)
        res["_p2"] = None
        # title / end cards rendered in the same browser
        card = ctx.new_page()
        res["card_page"] = card
        res["_ctx"] = (pw, browser, ctx)
        _render_cards(card, res, day_iso, out_dir)
        browser.close()
    return res


CARD_CSS = """body{margin:0;width:1600px;height:1000px;background:#0d1117;color:#e6edf3;font-family:system-ui,Segoe UI,Arial,sans-serif;
display:flex;flex-direction:column;justify-content:center;padding:0 110px;box-sizing:border-box}
h1{font-size:64px;margin:0 0 24px}h2{font-size:40px;margin:0 0 18px;color:#9fb3c8;font-weight:500}
p{font-size:34px;line-height:1.45;margin:6px 0}b{color:#7ee2b8}.bad{color:#ff8a8a}"""


def _render_cards(card, res, day_iso, out_dir):
    d = datetime.date.fromisoformat(day_iso)
    label = d.strftime("%a %d %b %Y")
    cards = {}
    for v, title in VIEWS:
        sub = ("the page's own 78 five-minute runs, basis %s" % BASIS_NAME[VIEW_BASIS]) if v == "density" else \
              ("left: Gamma, 0DTE book · right: %s, All book (0DTE + Convex 08:35 near book) · basis %s"
               % ({"charm": "Charm", "vanna": "Vanna", "chg": "Change (last 30 min)"}[v], BASIS_NAME[VIEW_BASIS]))
        html = "<html><head><style>%s</style></head><body><h2>SPX replay · %s · Convex (NAS)</h2><h1>%s</h1><p>%s</p><p>09:31 → 16:00, a frame every few minutes, no look-ahead</p></body></html>" % (CARD_CSS, label, title, sub)
        card.set_content(html)
        fp = out_dir / "frames" / ("card_%s.jpg" % v)
        card.screenshot(path=str(fp), type="jpeg", quality=88)
        cards[v] = fp
    res["cards"] = cards


def render_end_card(lines, day_iso, out_dir):
    """End card with the score, rendered after scoring (second short browser session)."""
    from playwright.sync_api import sync_playwright
    d = datetime.date.fromisoformat(day_iso)
    html = "<html><head><style>%s</style></head><body><h2>SPX replay · %s · score</h2>%s</body></html>" % (
        CARD_CSS, d.strftime("%a %d %b %Y"), "".join("<p>%s</p>" % l for l in lines))
    fp = out_dir / "frames" / "card_end.jpg"
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=CHROME_ARGS)
        p = b.new_page(viewport={"width": 1600, "height": 1000})
        p.set_content(html)
        p.screenshot(path=str(fp), type="jpeg", quality=88)
        b.close()
    return fp


# ------------------------------------------------------------------ stage 4: seven locked tests
def seven_tests(cols, quotes, spx_flow, basis):
    col = {c["t"]: c for c in cols}
    F = spx_flow

    def spot_at(m):
        for q in range(4):
            j = F.get(hh(m - q))
            if j and j.get("x"):
                return j["x"]
        return None

    def lv(m):
        t = m - (m % 2)
        while t >= 570 and t not in col:
            t -= 2
        c = col.get(t)
        S = spot_at(m)
        if not c or S is None:
            return None
        gz = min(c["gz"], key=lambda z: abs(z - S)) if c["gz"] else None
        vt = vb = None
        if len(c["vz"]) == 2:
            vt, vb = max(c["vz"]), min(c["vz"])
        return {"S": S, "gz": gz, "vt": vt, "vb": vb, "gpk": c["gpk"]}

    def quote(m, k, cp):
        for q in range(4):
            t = hh(m - q)
            if t in quotes and str(int(k)) in quotes[t]:
                cb, ca, pb, pa = quotes[t][str(int(k))]
                return (cb, ca) if cp == "C" else (pb, pa)
        return None

    def r5(x):
        return 5 * round(x / 5)

    def sd_at(m):
        j = None
        for q in range(4):
            j = F.get(hh(m - q))
            if j:
                break
        if not j:
            return None
        q = min(range(len(j["k"])), key=lambda i: abs(j["k"][i] - j["x"]))
        st_ = (j["cm"][q] or 0) + (j["pm"][q] or 0)
        return st_ / 0.7979 if st_ > 0 else None

    def price(m, legs, open_side):
        tot = 0
        for q, k, cp in legs:
            bq = quote(m, k, cp)
            if not bq or bq[1] is None or bq[0] is None:
                return None
            b, a = bq
            if open_side:
                tot += q * (a if q > 0 else b)
            else:
                tot += q * (b if q > 0 else a)
        return tot

    def run_trade(name, m0, legs, exit_rule, t_exit):
        c0 = price(m0, legs, True)
        if c0 is None:
            return None
        for m in range(m0 + 1, t_exit + 1):
            v = price(m, legs, False)
            if v is None:
                continue
            r = exit_rule(c0, v)
            if r:
                return {"test": name, "in": hh(m0), "out": hh(m), "why": r, "open": round(c0, 2), "close": round(v, 2), "pl": round((v - c0) * 100, 0)}
        m = t_exit
        while m > m0 and price(m, legs, False) is None:
            m -= 1
        v = price(m, legs, False)
        if v is None:
            return None
        return {"test": name, "in": hh(m0), "out": hh(m), "why": "time", "open": round(c0, 2), "close": round(v, 2), "pl": round((v - c0) * 100, 0)}

    trades = []

    def fly_legs(K, w, S):
        cp = "C" if K >= S else "P"
        return [(1, K - w, cp), (-2, K, cp), (1, K + w, cp)]

    def fly_exit(c0, v):
        return "target +25%" if v >= c0 * 1.25 else ("stop -50%" if v <= c0 * 0.5 else None)

    # tests 1-4: transition-touch trigger
    m, cool = 600, -1
    while m <= 780:
        x = lv(m)
        if x and m >= cool:
            trans = [z for z in (x["gz"], x["vt"], x["vb"]) if z is not None]
            if any(abs(x["S"] - z) <= 5 for z in trans):
                w = 25 if m < 660 else 20
                for name, K in (("1 Fly @ vanna top", x["vt"]), ("2 Fly @ gamma zero", x["gz"]), ("3 Fly @ vanna bottom", x["vb"])):
                    if K is None:
                        continue
                    t = run_trade(name, m, fly_legs(r5(K), w, x["S"]), fly_exit, 930)
                    if t:
                        t.update(level=round(K, 1), spot=x["S"])
                        trades.append(t)
                sd = sd_at(m)
                if x["gz"] is not None and sd and abs(x["S"] - x["gz"]) >= sd:
                    t = run_trade("4 Fly @ gamma zero +1SD", m, fly_legs(r5(x["gz"]), w, x["S"]), fly_exit, 930)
                    if t:
                        t.update(level=round(x["gz"], 1), spot=x["S"])
                        trades.append(t)
                trades.append({"test": "_trigger", "in": hh(m), "spot": x["S"], "near": [round(z, 1) for z in trans if abs(x["S"] - z) <= 5],
                               "sd": sd and round(sd, 1), "gz": x["gz"] and round(x["gz"], 1)})
                cool = m + 60
        m += 1
    # test 5
    for m in (571, 600, 630, 660, 690):
        x = lv(m)
        if not x or x["vt"] is None or x["vb"] is None:
            trades.append({"test": "5 Credit spread", "in": hh(m), "why": "no vanna rails" if x else "no data"})
            continue
        sc = 5 * math.ceil((x["vt"] + 20) / 5)
        sp = 5 * math.floor((x["vb"] - 20) / 5)
        legs = [(-1, sc, "C"), (1, sc + 25, "C"), (-1, sp, "P"), (1, sp - 25, "P")]
        c0 = price(m, legs, True)
        if c0 is None:
            continue
        credit = -c0

        def ex(c0_, v, credit=credit):
            cost = -v
            return "target 50%" if cost <= 0.5 * credit else ("stop 2x credit" if cost >= 2 * credit else None)
        t = run_trade("5 Credit spread", m, legs, ex, 840)
        if t:
            t.update(level="%s/%s" % (round(x["vt"], 1), round(x["vb"], 1)), spot=x["S"],
                     strikes="%d/%d C, %d/%d P" % (sc, sc + 25, sp, sp - 25), credit=round(credit, 2))
            trades.append(t)
    # test 6
    for m in (571, 600, 630):
        x = lv(m)
        if not x or x["gpk"] is None:
            continue
        kl = 5 * math.ceil((x["gpk"] + 20) / 5)
        legs = [(1, kl, "C"), (-1, kl + 25, "C")]
        t = run_trade("6 Debit 1:5", m, legs, lambda c0, v: "target 4x" if v >= 4 * c0 else None, 900)
        if t:
            t.update(level=x["gpk"], spot=x["S"], strikes="%d/%d C" % (kl, kl + 25))
            trades.append(t)
    # test 7
    for m in [571] + list(range(600, 781, 30)):
        x = lv(m)
        if not x or x["gz"] is None:
            continue
        K = r5(x["gz"])
        legs = [(-1, K, "C"), (-1, K, "P"), (1, K + 25, "C"), (1, K - 25, "P")]
        c0 = price(m, legs, True)
        if c0 is None:
            continue
        credit = -c0
        if credit < 18.0:
            trades.append({"test": "7 Old fly", "in": hh(m), "why": "credit %.2f < 18.00 - no trade" % credit, "level": round(x["gz"], 1), "spot": x["S"]})
            continue
        t = run_trade("7 Old fly", m, legs, lambda c0, v: "bought back <= $1,000" if -v <= 10.0 else None, 840)
        if t:
            t.update(level=round(x["gz"], 1), spot=x["S"], credit=round(credit, 2))
            trades.append(t)

    def risk(t):
        if t["test"].startswith(("1", "2", "3", "4", "6")):
            return t["open"] * 100
        return (25 - t.get("credit", 0)) * 100

    summary = []
    for n in TEST_NAMES:
        ts = [t for t in trades if t["test"] == n and "pl" in t]
        summary.append({"test": n, "n": len(ts), "risk": round(sum(risk(t) for t in ts)), "pl": round(sum(t["pl"] for t in ts)),
                        "wins": sum(1 for t in ts if t["pl"] > 0)})
    tot = {"n": sum(s["n"] for s in summary), "risk": sum(s["risk"] for s in summary), "pl": sum(s["pl"] for s in summary)}
    tot["pct50k"] = round(tot["pl"] / 50000 * 100, 2)
    return {"basis": basis, "conv": CONV, "trades": trades, "summary": summary, "total": tot}


# ------------------------------------------------------------------ stage 5: score
def score_density(den, close):
    runs = den.get("runs") or []
    if not runs or close is None:
        return {}
    cz = 5 * math.floor(close / 5)
    inside = [r for r in runs if r.get("p10") is not None and r["p10"] <= close <= r["p90"]]
    hits = [r for r in runs if r.get("zone") == cz]
    pz = [r["pZone"] for r in runs if r.get("pZone") is not None]
    med_err = sorted(abs(r["med"] - close) for r in runs if r.get("med") is not None)
    naive = sorted(abs(r["spot"] - close) for r in runs if r.get("spot") is not None)
    mid = lambda a: a[len(a) // 2] if a else None
    r0 = runs[0]
    ch = den.get("charm") or []
    tot = ch[-1][1] if ch else None
    after = None
    if ch:
        b14 = [v for t, v in ch if t < 840]
        after = tot - (b14[-1] if b14 else 0)
    return {"close": close, "close_zone": cz, "n": len(runs), "skipped": den.get("skipped") or [],
            "inside": len(inside), "zone_hits": [r["t"] for r in hits], "avg_pzone": (sum(pz) / len(pz)) if pz else None,
            "med_err": mid(med_err), "naive_err": mid(naive),
            "cone0": {"t": r0["t"], "spot": r0["spot"], "med": r0["med"], "p10": r0["p10"], "p90": r0["p90"], "zone": r0["zone"],
                      "pZone": r0["pZone"], "hit": r0.get("zone") == cz, "inside": r0["p10"] <= close <= r0["p90"]},
            "charm_bn": tot, "charm_after14_bn": after}


def score_charm(clock):
    if not clock:
        return {}
    buy = [c for c in clock if c["es"] > 0]
    pk = max(clock, key=lambda c: abs(c["es"]))
    # after 15:50 tau -> 0 and the function combs (sign flips every point): reported, not read
    changes, late = [], 0
    for i in range(1, len(clock)):
        if (clock[i]["es"] > 0) != (clock[i - 1]["es"] > 0):
            if clock[i]["t"] >= "15:50":
                late += 1
                continue
            changes.append("%s %s" % (clock[i]["t"], "sell->BUY" if clock[i]["es"] > 0 else "buy->SELL"))
    # the flip nearest spot, 10:00-15:00, minutes with at most 2 flips (more = the comb)
    near = []
    for c in clock:
        if "10:00" <= c["t"] <= "15:00" and 0 < len(c["flips"]) <= 2:
            near.append(min(c["flips"], key=lambda f: abs(f - c["S"])))
    near.sort()
    q = lambda a, p: a[min(len(a) - 1, int(p * len(a)))] if a else None
    return {"n": len(clock), "buy_min": len(buy), "sell_min": len(clock) - len(buy), "peak": pk, "changes": changes, "late_changes": late,
            "flip_med": q(near, 0.5), "flip_lo": q(near, 0.1), "flip_hi": q(near, 0.9), "flip_n": len(near)}


def tests_table(rows):
    """rows: {test: (n, risk, pl)} -> the 10/26-27 table layout: test, n, risk, P/L, avg, % of $50k."""
    out = ["<div class='wrap'><table><tr><th>test</th><th>n</th><th>risk</th><th>P/L</th><th>avg</th><th>% of $50k</th></tr>"]
    tn = tr = tp = 0
    for name in TEST_NAMES:
        n, rk, pl = rows.get(name, (0, 0, 0))
        tn, tr, tp = tn + n, tr + rk, tp + pl
        cls = "up" if pl > 0 else "dn" if pl < 0 else ""
        out.append("<tr><td>%s</td><td>%d</td><td>$%s</td><td class='%s'>%s</td><td>%s</td><td class='%s'>%+.2f%%</td></tr>"
                   % (name, n, fmt(rk), cls, money(pl) if n else "–", money(pl / n) if n else "–", cls, pl / BOOK * 100))
    cls = "up" if tp > 0 else "dn" if tp < 0 else ""
    out.append("<tr><td><b>TOTAL (all 7)</b></td><td><b>%d</b></td><td><b>$%s</b></td><td class='%s'><b>%s</b></td><td></td><td class='%s'><b>%+.2f%%</b></td></tr></table></div>"
               % (tn, fmt(tr), cls, money(tp), cls, tp / BOOK * 100))
    return "".join(out)


def running_tests(out_root, day_iso):
    """Sum every nightly seven_tests.json up to this day, per basis per test; the volume basis
    starts from the 10/26-27 table (SEED)."""
    acc = {b: {n: [0, 0, 0] for n in TEST_NAMES} for b in BASES}
    days = []
    for p in sorted(out_root.glob("2*/seven_tests.json")):
        d = p.parent.name
        d_iso = "%s-%s-%s" % (d[:4], d[4:6], d[6:8])
        if d_iso > day_iso or d_iso < SEED["since"]:
            continue
        try:
            tj = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        days.append(d_iso)
        for b in BASES:
            for s in (tj.get(b) or {}).get("summary", []):
                a = acc[b].get(s["test"])
                if a:
                    a[0] += s["n"]; a[1] += s["risk"]; a[2] += s["pl"]
    run = {"days": days, "first_day": days[0] if days else day_iso}
    for b in BASES:
        rows = {n: tuple(v) for n, v in acc[b].items()}
        if b == SEED["basis"]:
            rows = {n: (rows[n][0] + SEED["tests"][n][0], rows[n][1] + SEED["tests"][n][1], rows[n][2] + SEED["tests"][n][2]) for n in TEST_NAMES}
        run[b] = rows
        run[b + "_tot"] = (sum(v[0] for v in rows.values()), sum(v[2] for v in rows.values()))
    nd = len(days)
    span = ", ".join(d[5:].replace("-", "/") for d in days) if nd <= 3 else "%s → %s" % (days[0][5:].replace("-", "/"), days[-1][5:].replace("-", "/"))
    run["label"] = "Mon 10/26 + Tue 10/27 (ToS feed) + NAS replay %s (%d day%s)" % (span, nd, "" if nd == 1 else "s")
    return run


def fmt(x, d=0):
    if x is None:
        return "–"
    return "{:,.{d}f}".format(x, d=d)


def money(x):
    if x is None:
        return "–"
    return ("+$" if x >= 0 else "−$") + "{:,.0f}".format(abs(x))


def img_tag(fp, width=1200):
    try:
        from PIL import Image
        im = Image.open(fp)
        im.thumbnail((width, width))
        buf = io.BytesIO()
        im.convert("RGB").save(buf, "JPEG", quality=80)
        b = buf.getvalue()
    except Exception:
        b = Path(fp).read_bytes()
    return '<img alt="" src="data:image/jpeg;base64,%s">' % base64.b64encode(b).decode()


def scorecard_html(day_iso, day_info, dens, charm, clock, tests, levels, meta, last_frames):
    d = datetime.date.fromisoformat(day_iso)
    c0 = dens.get("cone0") or {}
    css = """:root{--bg:#ffffff;--fg:#14181f;--mute:#5b6675;--line:#d9dee5;--card:#f5f7fa;--up:#0f7b55;--dn:#b42318}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--fg:#e6edf3;--mute:#9aa7b4;--line:#2b333d;--card:#151b23;--up:#5ad19a;--dn:#ff8a8a}}
body{background:var(--bg);color:var(--fg);font:18px/1.55 system-ui,Segoe UI,Arial,sans-serif;margin:0;padding:24px 16px 60px}
main{max-width:1180px;margin:0 auto}h1{font-size:32px;margin:0 0 4px}h2{font-size:24px;margin:34px 0 10px;border-bottom:1px solid var(--line);padding-bottom:6px}
.sub{color:var(--mute);font-size:17px}.big{font-size:22px}.up{color:var(--up)}.dn{color:var(--dn)}
table{border-collapse:collapse;width:100%;font-size:17px;margin:8px 0}th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:right}
th:first-child,td:first-child{text-align:left}th{color:var(--mute);font-weight:600}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 18px;margin:10px 0}
img{max-width:100%;border:1px solid var(--line);border-radius:8px;margin:8px 0}details{margin:8px 0}summary{cursor:pointer;color:var(--mute)}
.wrap{overflow-x:auto}code{font-size:15px}"""
    h = ["<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
         "<title>SPX Replay %s</title><style>%s</style></head><body><main>" % (day_iso, css)]
    h.append("<h1>SPX replay · %s</h1><div class='sub'>Convex (NAS) data only · the Convex cockpit page run headless · map basis %s · built %s ET</div>"
             % (d.strftime("%A %d %b %Y"), BASIS_NAME[VIEW_BASIS], meta["built"]))
    h.append("<div class='card big'>Open %s · High %s · Low %s · <b>Close %s</b> · range %s pts (Convex minute log, 09:31–16:00)</div>"
             % (fmt(day_info["open"], 2), fmt(day_info["high"], 2), fmt(day_info["low"], 2), fmt(day_info["close"], 2), fmt(day_info["high"] - day_info["low"], 1)))
    # density
    h.append("<h2>Density cone</h2>")
    if dens:
        h.append("<div class='card big'>09:31 cone: median %s, P10–P90 %s–%s, most likely 5-pt zone %s–%s (%.0f%%). Close zone %s–%s: <b class='%s'>%s</b>.</div>"
                 % (fmt(c0.get("med")), fmt(c0.get("p10")), fmt(c0.get("p90")), fmt(c0.get("zone")), fmt((c0.get("zone") or 0) + 5),
                    100 * (c0.get("pZone") or 0), fmt(dens["close_zone"]), fmt(dens["close_zone"] + 5),
                    "up" if c0.get("hit") else "dn", "HIT" if c0.get("hit") else "miss"))
        h.append("<table><tr><th>measure</th><th>result</th></tr>")
        h.append("<tr><td>Close inside P10–P90</td><td>%d of %d runs</td></tr>" % (dens["inside"], dens["n"]))
        h.append("<tr><td>Top zone = close zone</td><td>%d of %d runs (average stated probability %.0f%%)%s</td></tr>"
                 % (len(dens["zone_hits"]), dens["n"], 100 * (dens["avg_pzone"] or 0), (": " + ", ".join(dens["zone_hits"])) if dens["zone_hits"] else ""))
        h.append("<tr><td>Median error vs close</td><td>%s pts (\"close = spot now\": %s pts)</td></tr>" % (fmt(dens["med_err"], 1), fmt(dens["naive_err"], 1)))
        h.append("<tr><td>Charm at realized spot (Density tab, OI, naive sign)</td><td>%s bn total, %s bn after 14:00</td></tr>"
                 % (fmt(dens.get("charm_bn"), 2), fmt(dens.get("charm_after14_bn"), 2)))
        if dens.get("skipped"):
            h.append("<tr><td>Runs skipped (no minute in the log)</td><td>%s</td></tr>" % ", ".join(str(k) for k in dens["skipped"]))
        h.append("</table>")
        h.append("<details><summary>Every half hour</summary><div class='wrap'><table><tr><th>run</th><th>spot</th><th>median</th><th>P10</th><th>P90</th><th>top zone</th><th>p</th><th>gamma zero</th></tr>")
        for r in meta["den_runs"]:
            if r["k"] % 30 == 0 or r["k"] == 385:
                h.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%.0f%%</td><td>%s</td></tr>"
                         % (r["t"], fmt(r["spot"], 1), fmt(r["med"], 1), fmt(r["p10"]), fmt(r["p90"]), fmt(r["zone"]), 100 * (r["pZone"] or 0), fmt(r.get("gz"))))
        h.append("</table></div></details>")
    else:
        h.append("<div class='card'>Density did not run.</div>")
    # charm clock
    h.append("<h2>Charm clock (levels_check.html functions)</h2>")
    if charm:
        pk = charm["peak"]
        h.append("<div class='card big'>Next-60-min dealer flow from time alone: <b>BUY on %d of %d minutes</b>, SELL on %d. Peak %s %s ES (~$%sm) at %s. "
                 "Charm flip nearest spot, 10:00–15:00: median %s (10–90%%: %s–%s, %d minutes). Sign changes before 15:50: %s%s.</div>"
                 % (charm["buy_min"], charm["n"], charm["sell_min"], "BUY" if pk["es"] > 0 else "SELL", fmt(abs(pk["es"])), fmt(pk["usd_m"]), pk["t"],
                    fmt(charm["flip_med"], 1), fmt(charm["flip_lo"], 1), fmt(charm["flip_hi"], 1), charm["flip_n"],
                    ", ".join(charm["changes"]) or "none",
                    (" (%d more after 15:50, where τ → 0 and the function combs: noise)" % charm["late_changes"]) if charm["late_changes"] else ""))
        h.append("<details><summary>Every half hour</summary><table><tr><th>time</th><th>spot</th><th>next 60 min</th><th>flips</th></tr>")
        for c in clock:
            if c["t"].endswith((":00", ":30")) or c["t"] == "15:59":
                h.append("<tr><td>%s</td><td>%s</td><td>%s %s ES (~$%sm)</td><td>%s</td></tr>" % (c["t"], fmt(c["S"], 1), "BUY" if c["es"] > 0 else "SELL",
                         fmt(abs(c["es"])), fmt(c["usd_m"]), " / ".join(fmt(f, 1) for f in c["flips"]) or "–"))
        h.append("</table></details>")
    # seven tests, in the layout of Mithil's 10/26-27 table
    h.append("<h2>Seven locked tests</h2><div class='sub'>$50,000 · 1 contract per signal · levels: the page's own 0DTE map per 2-min column · fills: Convex bid/ask, buy at ask / sell at bid.</div>")
    run = meta.get("running") or {}
    if run.get("vol"):
        h.append("<h3>Running total, volume basis · %s</h3>" % run["label"])
        h.append(tests_table(run["vol"]))
    for b in ("vol", "oiv", "oi"):
        if b not in tests:
            continue
        rows = {s["test"]: (s["n"], s["risk"], s["pl"]) for s in tests[b]["summary"]}
        tbl = "<h3>Today · %s basis%s</h3>%s" % (BASIS_NAME[b], " (the running basis)" if b == "vol" else "", tests_table(rows))
        h.append(tbl if b == "vol" else "<details><summary>Today · %s basis · total %s on %d trades</summary>%s</details>"
                 % (BASIS_NAME[b], money(tests[b]["total"]["pl"]), tests[b]["total"]["n"], tbl))
    for b in ("oiv", "oi"):
        if run.get(b):
            h.append("<details><summary>Running total · %s basis · since %s</summary>%s</details>" % (BASIS_NAME[b], run["first_day"], tests_table(run[b])))
    for b in BASES:
        tr = [t for t in tests.get(b, {}).get("trades", []) if "pl" in t]
        h.append("<details><summary>Trades, basis %s (%d)</summary><div class='wrap'><table><tr><th>test</th><th>in</th><th>out</th><th>why</th><th>open</th><th>close</th><th>P/L</th><th>level</th><th>spot</th></tr>" % (BASIS_NAME[b], len(tr)))
        for t in tr:
            h.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                     % (t["test"], t["in"], t["out"], t["why"], t["open"], t["close"], money(t["pl"]), t.get("level", ""), fmt(t.get("spot"), 1)))
        h.append("</table></div></details>")
    h.append("<details><summary>Conventions where the spec is silent (v1, fixed 2026-10-28, not tuned)</summary><ul>%s</ul></details>"
             % "".join("<li><b>%s</b>: %s</li>" % (k, v) for k, v in CONV.items()))
    # map levels
    h.append("<h2>Map lines (0DTE, basis %s)</h2>" % BASIS_NAME[VIEW_BASIS])
    lv = (levels or {}).get(VIEW_BASIS) or {}
    if lv.get("cols"):
        h.append("<div class='wrap'><table><tr><th>time</th><th>gamma zero(s)</th><th>vanna zeros</th><th>charm zeros</th><th>gamma peak</th></tr>")
        for c in lv["cols"]:
            if c["t"] % 30 == 0:
                h.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (hh(c["t"]), " / ".join(fmt(z) for z in c["gz"]) or "–",
                         " / ".join(fmt(z) for z in c["vz"]) or "–", " / ".join(fmt(z) for z in c["cz"]) or "–", fmt(c["gpk"])))
        h.append("</table></div>")
    # pictures
    h.append("<h2>16:00 frames</h2>")
    for v, title in VIEWS:
        if v in last_frames:
            h.append("<div class='sub'>%s</div>%s" % (title, img_tag(last_frames[v])))
    # inputs
    h.append("<h2>Inputs</h2><div class='card'><code>%s</code></div>" % "<br>".join("%s: %s" % (k, v) for k, v in meta["inputs"].items()))
    h.append("</main></body></html>")
    return "\n".join(h)


# ------------------------------------------------------------------ video
def find_ffmpeg(out_root):
    """(path, kind): system ffmpeg with libx264 -> mp4; else Playwright's ffmpeg (VP8) -> webm."""
    sysff = shutil.which("ffmpeg")
    if sysff:
        try:
            enc = subprocess.run([sysff, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=20).stdout
            if "libx264" in enc:
                return sysff, "mp4"
            if "libvpx" in enc:
                return sysff, "webm"
        except Exception:
            pass
    cands = []
    cache = out_root / ".bin" / "ffmpeg-linux"
    cands.append(cache)
    for base in (os.environ.get("PLAYWRIGHT_BROWSERS_PATH"), str(Path.home() / ".cache" / "ms-playwright"), "/ms-playwright", "/opt/pw-browsers"):
        if base and Path(base).is_dir():
            cands += sorted(Path(base).glob("ffmpeg-*/ffmpeg-linux"))
    for c in cands:
        if Path(c).is_file() and os.access(c, os.X_OK):
            return str(c), "webm"
    # not in the image: fetch Playwright's own build (~2 MB) once, keep a copy under reports/replay/.bin
    try:
        log("ffmpeg: python -m playwright install ffmpeg")
        subprocess.run([sys.executable, "-m", "playwright", "install", "ffmpeg"], capture_output=True, timeout=300)
        for base in (str(Path.home() / ".cache" / "ms-playwright"), os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or ""):
            for c in sorted(Path(base).glob("ffmpeg-*/ffmpeg-linux")) if base and Path(base).is_dir() else []:
                cache.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(c, cache)
                os.chmod(cache, 0o755)
                return str(cache), "webm"
    except Exception as exc:
        log("ffmpeg install failed: %s" % exc)
    return None, None


def make_video(seq, fps, out_path_noext, out_root):
    ff, kind = find_ffmpeg(out_root)
    if not ff:
        return None, "no ffmpeg"
    out = Path(str(out_path_noext) + "." + kind)
    if kind == "mp4":
        enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    else:
        # Playwright's ffmpeg build: pipe/file protocols only, VP8 encoder, WebM muxer (tested 2026-10-28)
        enc = ["-c:v", "vp8", "-qmin", "0", "-qmax", "50", "-crf", "8", "-b:v", "2M", "-deadline", "good", "-cpu-used", "4",
               "-threads", "2", "-pix_fmt", "yuv420p"]
    cmd = [ff, "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(fps), "-c:v", "mjpeg", "-i", "pipe:0", "-an"] + enc + [str(out)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for fp in seq:
            p.stdin.write(Path(fp).read_bytes())
        p.stdin.close()
    except BrokenPipeError:
        pass
    err = p.stderr.read().decode(errors="replace")
    rc = p.wait(timeout=900)
    if rc != 0 or not out.is_file():
        return None, "ffmpeg rc %s %s" % (rc, err[:300])
    return out, kind


# ------------------------------------------------------------------ history
HIST_COLS = ["date", "view_basis", "open", "high", "low", "close", "cone0_med", "cone0_p10", "cone0_p90", "cone0_zone", "close_zone",
             "cone0_hit", "inside_n", "runs_n", "zonehit_n", "avg_pzone", "med_err", "naive_err", "charm_bn", "charm_after14_bn",
             "clock_buy_min", "clock_n", "clock_peak_es", "clock_peak_t", "oi_n", "oi_pl", "oiv_n", "oiv_pl", "vol_n", "vol_pl",
             "flow_minutes", "week_src", "page_md5"]


def write_history(path, row):
    rows = []
    if path.is_file():
        with open(path, newline="", encoding="utf-8") as fh:
            rows = [r for r in csv.DictReader(fh) if r.get("date") != row["date"]]
    rows.append(row)
    rows.sort(key=lambda r: r["date"])
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=HIST_COLS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    os.replace(tmp, path)


def telegram(text, files):
    try:
        sys.path.insert(0, str(HERE.parent))
        from trading_intel.config import Settings
        from trading_intel.clients.telegram import TelegramClient
        tc = TelegramClient(Settings())
        if not tc.enabled:
            log("telegram not configured")
            return False
        ok = tc.send_message(text)
        for fp, cap in files:
            if fp and Path(fp).is_file():
                if Path(fp).stat().st_size > 49e6:
                    log("telegram: %s too big (%.0f MB), kept on the NAS only" % (fp, Path(fp).stat().st_size / 1e6))
                    continue
                ok = tc.send_document(fp, caption=cap) and ok
        return ok
    except Exception as exc:
        log("telegram failed: %s" % exc)
        return False


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--data", default=str(CVX / "data"))
    ap.add_argument("--out", default=str(HERE.parent / "reports" / "replay"))
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--fps", type=int, default=4)
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--no-video", action="store_true")
    ap.add_argument("--extract-only", action="store_true")
    ap.add_argument("--extract", help="reuse an extract json instead of reading the raw file")
    ap.add_argument("--flow", help="flow file override")
    ap.add_argument("--ti", help="ti.json override")
    ap.add_argument("--page", help="cockpit page override")
    args = ap.parse_args()

    t_all = time.time()
    now = et_now()
    day = datetime.datetime.strptime(args.date, "%Y%m%d").date() if args.date else now.date()
    day_iso, ymd = day.isoformat(), day.strftime("%Y%m%d")
    data = Path(args.data)
    out_root = Path(args.out)
    out_dir = out_root / ymd
    out_dir.mkdir(parents=True, exist_ok=True)
    log("cvx_replay %s  data=%s  out=%s" % (day_iso, data, out_dir))
    if day.weekday() >= 5 and not args.date:
        log("weekend - nothing to do")
        return 0

    raw = data / ("raw_%s.ndjson.gz" % ymd)
    flow_p = Path(args.flow) if args.flow else data / ("flow_%s.ndjson" % ymd)
    stage = "extract"
    try:
        if args.extract:
            ex = json.load(open(args.extract, encoding="utf-8"))
        else:
            if not raw.is_file():
                log("no raw file %s - market holiday or the collector did not run" % raw)
                if not args.no_telegram:
                    telegram("<b>SPX replay %s</b>: no Convex raw file on the NAS - nothing replayed." % day_iso, [])
                return 0
            t0 = time.time()
            ex = extract(raw, day)
            log("extract: %d idx pulls, OI at %s, %d quote minutes, %.0fs%s" % (ex["n_idx"], ex["oi_ts"], len(ex["q"]), time.time() - t0,
                ("  EXPIRY MISMATCH " + ", ".join(ex["exp_bad"])) if ex["exp_bad"] else ""))
            with open(out_dir / "extract.json", "w", encoding="utf-8") as fh:
                json.dump(ex, fh, separators=(",", ":"))
        if args.extract_only:
            return 0
        if not ex.get("oi") or "SPX" not in ex["oi"]:
            raise RuntimeError("no SPX 0DTE OI in the raw file")

        stage = "inputs"
        flow, flow_lines = load_flow(flow_p, day_iso)
        spx = flow.get("SPX") or {}
        if len(spx) < 60:
            raise RuntimeError("only %d SPX minutes in %s" % (len(spx), flow_p))
        rth = {t: j for t, j in spx.items() if "09:31" <= t <= T_END}
        xs = [j["x"] for t, j in sorted(rth.items())]
        day_info = {"open": xs[0], "high": max(xs), "low": min(xs), "close": xs[-1], "close_t": max(rth)}
        missing = [hh(m) for m in range(571, mm(T_END) + 1) if hh(m) not in spx]
        tmpl = {}
        lp = data / "live.json"
        if lp.is_file():
            try:
                tj = json.load(open(lp, encoding="utf-8"))
                if str(tj.get("ts", ""))[:10] == day_iso:
                    tmpl = tj
            except Exception:
                pass
        ti_p = Path(args.ti) if args.ti else data / "ti.json"
        ti_bytes, week_src, ti_note = None, "tos", "no ti.json"
        if ti_p.is_file():
            ti_bytes = ti_p.read_bytes()
            try:
                tj = json.loads(ti_bytes)
                nb = (tj.get("near_book") or {}).get("SPX") or {}
                asof = str(nb.get("as_of") or tj.get("asof") or "")
                if nb.get("rows") and asof[:10] == day_iso:
                    week_src, ti_note = "convex", "near_book %s, %d SPX rows" % (asof[:16], len(nb["rows"]))
                else:
                    ti_note = "near_book as_of %s is not %s - All book uses the writer's week book" % (asof[:16] or "?", day_iso)
            except Exception as exc:
                ti_note = "ti.json unreadable: %s" % exc
        page_p = Path(args.page) if args.page else (CVX / "cvx_cockpit.html" if (CVX / "cvx_cockpit.html").is_file() else data / "cvx_cockpit.html")
        page_src = page_p.read_text(encoding="utf-8")
        if page_src.count(HOOK) != 1 or page_src.count(DEN_HOOK) != 1:
            raise RuntimeError("cockpit page %s has no debug hook / Density tab" % page_p)
        page_html = page_src.replace(HOOK, REPLAY_HOOK).replace(DEN_HOOK, DEN_REPLAY).encode("utf-8")

        stage = "browser"
        res = replay_page(args, day_iso, page_html, flow, flow_lines, ex["oi"], tmpl, ex.get("week"), ti_bytes, week_src,
                          data, out_dir, args.step)
        log("browser done in %ss, hook %s, view %s" % (res["browser_s"], res["hook_ok"], res.get("view_state")))
        errs = [c for c in res["console"] if "pageerror" in c or "[density]" in c]
        if errs:
            log("page errors: %s" % " | ".join(errs[:5]))

        stage = "score"
        den = res["density"]
        dens = score_density(den, day_info["close"])
        clock = res["charm"]
        charm = score_charm(clock)
        tests = {}
        for b in BASES:
            lvb = (res["levels"] or {}).get(b)
            if lvb and lvb.get("cols"):
                tests[b] = seven_tests(lvb["cols"], ex["q"], spx, b)
        for name, obj in (("density_runs.json", den), ("charm_clock.json", clock), ("map_levels.json", res["levels"]), ("seven_tests.json", tests)):
            with open(out_dir / name, "w", encoding="utf-8") as fh:
                json.dump(obj, fh, separators=(",", ":"))
        hist_row = {"date": day_iso, "view_basis": VIEW_BASIS, "open": day_info["open"], "high": day_info["high"], "low": day_info["low"],
                    "close": day_info["close"], "flow_minutes": len(rth), "week_src": week_src, "page_md5": md5_bytes(page_src.encode())[:12]}
        if dens:
            c0 = dens["cone0"]
            hist_row.update(cone0_med=round(c0["med"], 2), cone0_p10=round(c0["p10"], 2), cone0_p90=round(c0["p90"], 2), cone0_zone=c0["zone"],
                            close_zone=dens["close_zone"], cone0_hit=int(c0["hit"]), inside_n=dens["inside"], runs_n=dens["n"],
                            zonehit_n=len(dens["zone_hits"]), avg_pzone=round(dens["avg_pzone"] or 0, 4), med_err=round(dens["med_err"], 2),
                            naive_err=round(dens["naive_err"], 2), charm_bn=round(dens["charm_bn"] or 0, 3),
                            charm_after14_bn=round(dens["charm_after14_bn"] or 0, 3))
        if charm:
            hist_row.update(clock_buy_min=charm["buy_min"], clock_n=charm["n"], clock_peak_es=charm["peak"]["es"], clock_peak_t=charm["peak"]["t"])
        for b in BASES:
            if b in tests:
                hist_row[b + "_n"] = tests[b]["total"]["n"]
                hist_row[b + "_pl"] = tests[b]["total"]["pl"]
        write_history(out_root / "history.csv", hist_row)
        running = running_tests(out_root, day_iso)

        stage = "video"
        lines = []
        if dens:
            c0 = dens["cone0"]
            lines.append("Close <b>%s</b> (Convex 16:00 print) · 09:31 cone %s–%s · top zone %s–%s → %s" % (fmt(day_info["close"], 2), fmt(c0["p10"]), fmt(c0["p90"]),
                         fmt(c0["zone"]), fmt(c0["zone"] + 5), "<b>HIT</b>" if c0["hit"] else "<span class='bad'>miss</span>"))
            lines.append("Close inside P10–P90 in %d of %d runs · top zone = close zone in %d" % (dens["inside"], dens["n"], len(dens["zone_hits"])))
        if charm:
            lines.append("Charm clock: BUY %d of %d minutes · peak %s ES at %s · flip ~%s" % (charm["buy_min"], charm["n"], fmt(charm["peak"]["es"]),
                         charm["peak"]["t"], fmt(charm["flip_med"])))
        if tests:
            lines.append("Seven tests today: " + " · ".join("%s %s (%d)" % (BASIS_NAME[b], money(tests[b]["total"]["pl"]), tests[b]["total"]["n"]) for b in ("vol", "oiv", "oi") if b in tests))
            n_, pl_ = running["vol_tot"]
            lines.append("Running since 10/26, volume basis: <b>%d trades %s (%+.2f%%)</b>" % (n_, money(pl_), pl_ / BOOK * 100))
        video, vkind = None, None
        last_frames = {v: fr[-1][1] for v, fr in res["frames"].items() if fr}
        if not args.no_video:
            end = render_end_card(lines, day_iso, out_dir)
            seq = []
            for v, _ in VIEWS:
                seq += [res["cards"][v]] * (args.fps * 2)
                seq += [fp for (T, fp) in res["frames"][v]]
                if res["frames"][v]:
                    seq += [res["frames"][v][-1][1]] * args.fps
            seq += [end] * (args.fps * 5)
            video, vkind = make_video(seq, args.fps, out_dir / ("spx_replay_%s" % ymd), out_root)
            log("video: %s %s" % (video, vkind))

        stage = "scorecard"
        meta = {"built": et_now().strftime("%Y-%m-%d %H:%M"), "den_runs": den.get("runs") or [], "running": running,
                "inputs": {"raw": "%s (%d idx pulls; OI from the %s pull)" % (raw.name, ex.get("n_idx", 0), (ex.get("oi_ts") or "")[11:19]),
                           "flow": "%s (%d SPX minutes 09:31–16:00; %d missing%s)" % (flow_p.name, len(rth), len(missing), (": " + ", ".join(missing[:30])) if missing else ""),
                           "All book": "weekSrc %s (%s)" % (week_src, ti_note),
                           "page": "%s md5 %s (replay copy: debug handle widened, no maths changed)" % (page_p.name, md5_bytes(page_src.encode())[:12]),
                           "charm clock": "levels_check.html functions verbatim (md5 %s)" % md5_bytes(CHARM_JS.encode())[:12],
                           "view": json.dumps(res.get("view_state")),
                           "density": "%d runs, %d skipped%s" % (len(den.get("runs") or []), len(den.get("skipped") or []), (", error: " + den["err"]) if den.get("err") else ""),
                           "video": "%s" % (video.name if video else vkind or "skipped"),
                           "runtime": "%.0f s" % (time.time() - t_all)}}
        html = scorecard_html(day_iso, day_info, dens, charm, clock, tests, res["levels"], meta, last_frames)
        sc = out_dir / ("scorecard_%s.html" % ymd)
        sc.write_text(html, encoding="utf-8")
        log("scorecard %s (%.1f MB)" % (sc, sc.stat().st_size / 1e6))
        # keep the disk light: the frames are inside the video
        if video:
            shutil.rmtree(out_dir / "frames", ignore_errors=True)

        stage = "telegram"
        if not args.no_telegram:
            msg = ["<b>SPX replay %s</b> (Convex / NAS, map basis %s)" % (day.strftime("%a %d %b"), BASIS_NAME[VIEW_BASIS])]
            msg.append("Close %s · range %s" % (fmt(day_info["close"], 2), fmt(day_info["high"] - day_info["low"], 1)))
            for l in lines:
                msg.append(l.replace("<span class='bad'>", "").replace("</span>", ""))
            if missing:
                msg.append("Convex minutes missing: %d" % len(missing))
            telegram("\n".join(msg), [(sc, "SPX replay scorecard %s" % day_iso), (video, "SPX replay video %s" % day_iso)])
        log("done in %.0f s" % (time.time() - t_all))
        return 0
    except Exception as exc:
        tb = traceback.format_exc()
        log("FAILED at %s\n%s" % (stage, tb))
        if not args.no_telegram:
            telegram("<b>SPX replay %s FAILED</b> at %s: %s" % (day_iso, stage, str(exc)[:300]), [])
        return 1


if __name__ == "__main__":
    sys.exit(main())
