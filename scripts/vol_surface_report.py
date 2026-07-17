"""Generate the vol-surface-changes HTML report (like the ^SPX board) from banked data.

Reads ``surface_snapshots`` (today vs the prior banked day) and writes a SELF-CONTAINED
dark HTML report — no Streamlit server, just open the file. Mirrors
``reports/SPX_vol_surface_preview.html`` but with LIVE banked data. Inline SVG only
(no CDN), per the html-report pattern. Descriptor only (FlashAlpha rule 4).

Usage (repo root, venv active):
    python scripts/vol_surface_report.py            # SPX
    python scripts/vol_surface_report.py QQQ
    python scripts/vol_surface_report.py SPY --no-open
"""

from __future__ import annotations

import json
import sys
import webbrowser
from pathlib import Path

import numpy as np

from trading_intel.config import get_settings
from trading_intel.dashboard.vol_surface_data import (
    changes_pivot,
    load_surface,
    surface_pivot,
    two_latest_dates,
)
from trading_intel.memory.db import make_session_factory


def _num(v) -> float | None:  # noqa: ANN001
    if v is None:
        return None
    f = float(v)
    return None if np.isnan(f) else round(f, 3)


def _footprint(session, symbol: str) -> dict | None:  # noqa: ANN001
    """Multi-day FIXED-STRIKE (front-week) vol footprint + GEX cross-check (the desk read)."""
    from trading_intel.dashboard.vol_surface_data import latest_net_gex, load_footprint_panel
    from trading_intel.vol.footprint import analyze_footprint

    panel = load_footprint_panel(session, symbol)
    if not panel:
        return None
    fr = analyze_footprint(
        call_ivs=panel["call"], put_ivs=panel["put"],
        net_gex=latest_net_gex(session, symbol), symbol=symbol,
    )

    def _k(v):  # noqa: ANN001, ANN202
        return None if v is None else round(float(v), 2)

    return {
        "headline": fr.headline, "narrative": fr.narrative, "regime": fr.regime,
        "gex_sign": fr.gex_sign, "confirms": fr.confirms_gex, "expiry": str(panel["expiry_date"]),
        "call_strike": _k(panel.get("call_strike")), "put_strike": _k(panel.get("put_strike")),
        "atm_strike": _k(panel.get("atm_strike")),
        "call": {"dir": fr.call.direction, "total_bp": fr.call.total_bp,
                 "per_day": fr.call.per_day_bp, "persistence": fr.call.persistence, "n": fr.call.n_days},
        "put": {"dir": fr.put.direction, "total_bp": fr.put.total_bp,
                "per_day": fr.put.per_day_bp, "persistence": fr.put.persistence, "n": fr.put.n_days},
    }


_GRID_STRIKES = 18  # near-money strikes shown in the surface/changes grid (readability)


def build_data(session, symbol: str) -> dict | None:  # noqa: ANN001
    dates = two_latest_dates(session, symbol)
    if not dates:
        return None
    ts_today = dates[0]
    ts_prior = dates[1] if len(dates) > 1 else None
    dft = load_surface(session, symbol, ts_today)
    if dft.empty:
        return None
    dfp = load_surface(session, symbol, ts_prior) if ts_prior else None

    spot = float(dft["spot"].dropna().iloc[0]) if dft["spot"].notna().any() else None

    ivp = surface_pivot(dft)  # strike (idx, high->low) x dte (cols), vol %
    chg = changes_pivot(dft, dfp) if dfp is not None else None

    # Keep a near-money window of strikes around spot so the grid stays readable.
    all_k = list(ivp.index)
    if spot is not None and len(all_k) > _GRID_STRIKES:
        keep = sorted(all_k, key=lambda k: abs(float(k) - spot))[:_GRID_STRIKES]
        grid_k = sorted(keep, reverse=True)  # high strike (upside) at top
    else:
        grid_k = all_k
    ivp = ivp.reindex(index=grid_k)
    if chg is not None and not chg.empty:
        chg = chg.reindex(index=grid_k, columns=ivp.columns)

    strikes = [float(k) for k in ivp.index]
    dtes = [int(c) for c in ivp.columns]
    iv = [[_num(x) for x in row] for row in ivp.values]
    chgv = (
        [[_num(x) for x in row] for row in chg.values]
        if (chg is not None and not chg.empty)
        else None
    )

    # Front-expiry skew, x = strike (fixed-strike compare to prior day).
    near = dft.sort_values("dte")["expiry_date"].iloc[0]
    ft = dft[dft["expiry_date"] == near].sort_values("strike")
    skew = {
        "strike": [float(k) for k in ft["strike"]],
        "live": [_num(v * 100) for v in ft["iv"]],
        "prior": [],
    }
    if dfp is not None:
        fp = dfp[dfp["expiry_date"] == near].set_index("strike")["iv"]
        skew["prior"] = [(_num(fp[k] * 100) if k in fp.index else None) for k in ft["strike"]]

    # ATM term structure: per expiry, the strike nearest that day's spot.
    term = {"dte": [], "live": [], "prior": []}

    def _atm_by_dte(df):  # noqa: ANN001, ANN202
        sp = float(df["spot"].dropna().iloc[0]) if df["spot"].notna().any() else spot
        out = {}
        for exp, g in df.groupby("expiry_date"):
            g = g.dropna(subset=["iv"])
            if g.empty:
                continue
            r = g.iloc[(g["strike"] - (sp if sp is not None else g["strike"].median())).abs().argmin()]
            out[exp] = (int(r["dte"]), float(r["iv"]))
        return out

    live_atm = _atm_by_dte(dft)
    prior_atm = _atm_by_dte(dfp) if dfp is not None else {}
    for exp in sorted(live_atm, key=lambda e: live_atm[e][0]):
        dte_, iv_ = live_atm[exp]
        term["dte"].append(dte_)
        term["live"].append(_num(iv_ * 100))
        pv = prior_atm.get(exp)
        term["prior"].append(_num(pv[1] * 100) if pv else None)

    return {
        "symbol": symbol, "curDate": str(ts_today), "priorDate": (str(ts_prior) if ts_prior else None),
        "spot": spot, "strikes": strikes, "dtes": dtes, "iv": iv, "chg": chgv,
        "skew": skew, "term": term, "footprint": _footprint(session, symbol),
    }


def build(symbol: str) -> str:
    """Write the vol-surface report HTML and return its path (for the MCP tool / CLI)."""
    symbol = symbol.strip().upper()
    out_dir = Path(__file__).resolve().parent.parent / "reports"
    out_dir.mkdir(exist_ok=True)
    with make_session_factory(get_settings())() as session:
        data = build_data(session, symbol)
    if data is None:
        out = out_dir / f"{symbol}_vol_surface_nodata.html"
        out.write_text(
            "<!doctype html><html><body style='background:#0a1230;color:#dbe4ff;"
            f"font-family:sans-serif;padding:40px'><h2>{symbol}: no surface data yet</h2>"
            "<p>Run <code>run_surface.bat</code> after the close (needs a live chain); "
            "two days gives the changes + the footprint read.</p></body></html>",
            encoding="utf-8",
        )
        return str(out)
    out = out_dir / f"{symbol}_vol_surface_{data['curDate']}.html"
    out.write_text(_TEMPLATE.replace("__DATA_JSON__", json.dumps(data)), encoding="utf-8")
    return str(out)


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    symbol = (args[0] if args else "SPX").upper()
    path = build(symbol)
    print(f"Wrote {path}")
    if "--no-open" not in sys.argv:
        try:
            webbrowser.open(Path(path).as_uri())
        except Exception:  # noqa: BLE001
            pass


_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vol Surface Changes</title>
<style>
:root{color-scheme:dark;}*{box-sizing:border-box;}
body{margin:0;padding:14px 16px 30px;background:#0a1230;color:#dbe4ff;font-family:-apple-system,"Segoe UI",Roboto,Arial,sans-serif;}
.wrap{max-width:1180px;margin:0 auto;}
.topbar{display:flex;align-items:center;gap:14px;border-bottom:1px solid #22336b;padding-bottom:8px;margin-bottom:12px;}
.chip{background:#12204d;border:1px solid #2a3f7a;border-radius:6px;padding:5px 11px;text-align:center;min-width:112px;}
.chip .l{font-size:9px;letter-spacing:.6px;color:#7c92d6;text-transform:uppercase;}.chip .v{font-size:15px;font-weight:800;color:#eaf1ff;}
.chip.cur{border-color:#2f6b46;}.chip.cur .v{color:#4ade80;}
h1{flex:1;text-align:center;margin:0;font-size:21px;font-weight:800;letter-spacing:1.5px;color:#eaf1ff;}.spx{color:#f59e0b;}
.grid{display:grid;grid-template-columns:1.35fr 1fr;gap:12px;}
.panel{background:#0e1a3e;border:1px solid #1f2f61;border-radius:9px;padding:10px 11px;}
.panel h2{margin:0 0 7px;font-size:10.5px;letter-spacing:1px;text-transform:uppercase;color:#8ea3e0;font-weight:700;text-align:center;}
table{width:100%;border-collapse:collapse;font-size:10.5px;font-variant-numeric:tabular-nums;}
th,td{padding:2px 4px;text-align:right;border:1px solid #14224a;}th{color:#9db0e8;font-weight:700;font-size:9.5px;background:#12204d;}
td.dcol{color:#c7d3f5;font-weight:700;background:#111e46;text-align:center;}.grouphdr th{background:#16224a;color:#c9d6ff;}
.rightcol{display:flex;flex-direction:column;gap:12px;}.box{width:100%;}.box svg{display:block;width:100%;height:100%;}
.h230{height:214px;}.h250{height:236px;}.row2{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:12px;}
.cap{color:#6f83bd;font-size:10px;margin-top:6px;text-align:center;}
.read{border-left:5px solid #64748b;margin-bottom:12px;}.read h3{margin:0 0 5px;font-size:14px;color:#eaf1ff;font-weight:800;}
.read p{margin:0;font-size:12.5px;color:#c7d3f5;line-height:1.55;}.read .stat{color:#7c92d6;font-size:10.5px;margin-top:7px;}
.read.ok{border-left-color:#22c55e;}.read.bad{border-left-color:#f59e0b;}
</style></head><body><div class="wrap">
<div class="topbar">
<div class="chip cur"><div class="l">Current</div><div class="v" id="cur"></div></div>
<div class="chip"><div class="l">Prior</div><div class="v" id="prior"></div></div>
<h1><span class="spx" id="sym"></span> VOLATILITY SURFACE CHANGES</h1>
<div class="chip"><div class="l">Spot</div><div class="v" id="spot"></div></div></div>
<div class="panel read" id="readpanel" style="display:none"></div>
<div class="grid">
<div class="panel"><h2>Vol surface · vol changes (Δ vs prior)</h2><div id="tbl"></div></div>
<div class="rightcol">
<div class="panel"><h2>Volatility surface (strike × expiry × IV)</h2><div class="box h230" id="surf"></div></div>
<div class="panel"><h2>Fixed-strike vol changes</h2><div class="box h230" id="bars"></div></div></div></div>
<div class="row2">
<div class="panel"><h2>Front-expiry vol skew (live vs prior)</h2><div class="box h250" id="skew"></div></div>
<div class="panel"><h2>Term structure (live vs prior)</h2><div class="box h250" id="term"></div></div></div>
<div class="cap" id="cap"></div></div>
<script>
const D=__DATA_JSON__;const FONT="-apple-system,'Segoe UI',Roboto,sans-serif";
function lerp(a,b,t){return a+(b-a)*t;}
let IVMIN=Infinity,IVMAX=-Infinity;D.iv.forEach(r=>r.forEach(v=>{if(v!=null){IVMIN=Math.min(IVMIN,v);IVMAX=Math.max(IVMAX,v);}}));
if(!isFinite(IVMIN)){IVMIN=8;IVMAX=20;}
function ivColor(iv){const t=Math.max(0,Math.min(1,(iv-IVMIN)/((IVMAX-IVMIN)||1)));return `rgb(${Math.round(lerp(21,250,t))},${Math.round(lerp(94,204,t))},${Math.round(lerp(117,21,t))})`;}
let CMAX=0.5;if(D.chg)D.chg.forEach(r=>r.forEach(v=>{if(v!=null)CMAX=Math.max(CMAX,Math.abs(v));}));
function chgBg(c){const a=Math.min(0.85,Math.abs(c)/(CMAX||1)*0.8+0.12);return c<0?`rgba(220,38,38,${a})`:`rgba(34,197,94,${a})`;}
document.getElementById("cur").textContent=D.curDate;document.getElementById("prior").textContent=D.priorDate||"—";
document.getElementById("sym").textContent="^"+D.symbol;document.getElementById("spot").textContent=D.spot!=null?Math.round(D.spot).toLocaleString():"n/a";
document.getElementById("cap").textContent="Banked surface_snapshots · today vs prior day · fixed-strike (each listed contract vs its own prior-day mark) · OTM-wing IV per strike. Descriptive (rule 4).";
(function(){const f=D.footprint;const el=document.getElementById("readpanel");if(!f){return;}
el.style.display="block";el.className="panel read"+(f.confirms===true?" ok":(f.confirms===false?" bad":""));
const gx=f.gex_sign?(f.gex_sign+(f.confirms===true?" · confirms":(f.confirms===false?" · CONTRADICTS":" · n/a"))):"n/a";
const cs=f.call_strike!=null?f.call_strike.toLocaleString():"—",ps=f.put_strike!=null?f.put_strike.toLocaleString():"—";
el.innerHTML=`<h3>The read — vol footprint · ${f.headline}</h3><p>${f.narrative}</p><div class="stat">Fixed strikes (front-week ${f.expiry}): call ${cs} / put ${ps} · GEX cross-check: ${gx}</div>`;})();
// table (limit to nearest 6 expiries for readability)
(function(){const nd=Math.min(6,D.dtes.length);const cols=D.dtes.slice(0,nd);
let h=`<table><tr class="grouphdr"><th></th><th colspan="${nd}">VOL SURFACE</th><th colspan="${nd}">VOL CHANGES</th></tr>`;
h+=`<tr><th>K</th>`+cols.map(c=>`<th>${c}d</th>`).join("")+cols.map(c=>`<th>${c}d</th>`).join("")+`</tr>`;
D.strikes.forEach((m,i)=>{h+=`<tr><td class="dcol">${m.toLocaleString()}</td>`;
for(let j=0;j<nd;j++){const v=D.iv[i][j];h+=v==null?`<td>·</td>`:`<td style="color:#0a1230;font-weight:700;background:${ivColor(v)}">${v.toFixed(2)}</td>`;}
for(let j=0;j<nd;j++){const c=D.chg?D.chg[i][j]:null;h+=c==null?`<td>·</td>`:`<td style="background:${chgBg(c)};color:#eef">${c>0?"+":""}${c.toFixed(2)}</td>`;}
h+=`</tr>`;});document.getElementById("tbl").innerHTML=h+`</table>`;})();
// iso 3D surface from D.iv
(function(){const W=400,H=214,N=D.strikes.length,M=D.dtes.length;const o={x:150,y:60,cx:Math.min(9,180/Math.max(N,1)),cy:5.2,cz:78};
const norm=v=>(v-IVMIN)/((IVMAX-IVMIN)||1);const iso=(i,j,hn)=>[o.x+(i-j)*o.cx,o.y+(i+j)*o.cy-hn*o.cz];
const gold=hn=>`rgb(${Math.round(lerp(120,250,hn))},${Math.round(lerp(80,205,hn))},${Math.round(lerp(30,25,hn))})`;
let s=`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" font-family="${FONT}" xmlns="http://www.w3.org/2000/svg">`;const quads=[];
for(let i=0;i<N-1;i++)for(let j=0;j<M-1;j++){const z=[D.iv[i][j],D.iv[i+1][j],D.iv[i+1][j+1],D.iv[i][j+1]];if(z.some(v=>v==null))continue;
const p=[iso(i,j,norm(z[0])),iso(i+1,j,norm(z[1])),iso(i+1,j+1,norm(z[2])),iso(i,j+1,norm(z[3]))];
quads.push({d:i+j,pts:p.map(q=>q[0].toFixed(1)+","+q[1].toFixed(1)).join(" "),hn:norm(z[0])});}
quads.sort((a,b)=>a.d-b.d);quads.forEach(q=>{s+=`<polygon points="${q.pts}" fill="${gold(q.hn)}" fill-opacity="0.55" stroke="#facc15" stroke-opacity="0.35" stroke-width="0.5"/>`;});
s+=`<text x="14" y="18" font-size="9" fill="#7c92d6">IV</text><text x="${o.x-70}" y="${H-6}" font-size="8.5" fill="#6f83bd">◀ strike</text><text x="${o.x+55}" y="${H-6}" font-size="8.5" fill="#6f83bd">expiry ▶</text>`;
document.getElementById("surf").innerHTML=s+`</svg>`;})();
// iso change bars from D.chg
(function(){const W=400,H=214;if(!D.chg){document.getElementById("bars").innerHTML=`<svg viewBox="0 0 ${W} ${H}"><text x="30" y="110" font-size="12" fill="#6f83bd">Needs a prior day to diff.</text></svg>`;return;}
const N=D.strikes.length,M=D.dtes.length;const o={x:150,y:150,cx:Math.min(10,190/Math.max(N,1)),cy:5.4};const iso=(i,j)=>[o.x+(i-j)*o.cx,o.y+(i+j)*o.cy];
let s=`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" font-family="${FONT}" xmlns="http://www.w3.org/2000/svg">`;const bars=[];
for(let i=0;i<N;i++)for(let j=0;j<M;j++){const c=D.chg[i][j];if(c==null)continue;bars.push({d:i+j,i,j,c});}
bars.sort((a,b)=>a.d-b.d);bars.forEach(b=>{const base=iso(b.i,b.j);const h=Math.min(70,Math.abs(b.c)/(CMAX||1)*44+2);const x=base[0],y=base[1];const col=b.c<0?"#dc2626":"#22c55e";
s+=`<polygon points="${x-3},${y} ${x+3},${y} ${x+3},${(y-h).toFixed(1)} ${x-3},${(y-h).toFixed(1)}" fill="${col}" fill-opacity="0.9"/><polygon points="${x-3},${(y-h).toFixed(1)} ${x+3},${(y-h).toFixed(1)} ${x+5},${(y-h-3).toFixed(1)} ${x-1},${(y-h-3).toFixed(1)}" fill="${col}" fill-opacity="0.6"/>`;});
s+=`<text x="14" y="18" font-size="9" fill="#7c92d6">Δvol</text><text x="${o.x-70}" y="${H-6}" font-size="8.5" fill="#6f83bd">◀ strike</text><text x="${o.x+52}" y="${H-6}" font-size="8.5" fill="#6f83bd">expiry ▶</text>`;
document.getElementById("bars").innerHTML=s+`</svg>`;})();
// 2D line helper (live gold vs prior grey)
function line2d(elId,xs,live,prior,xlab){const W=460,H=236,padL=40,padR=20,padT=14,padB=26;
const all=live.concat(prior||[]).filter(v=>v!=null);if(!all.length){document.getElementById(elId).innerHTML="";return;}
const lo=Math.min(...all)-0.5,hi=Math.max(...all)+0.5;const X=i=>padL+(W-padL-padR)*i/((xs.length-1)||1),Y=v=>padT+(H-padT-padB)*(1-(v-lo)/((hi-lo)||1));
let s=`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" font-family="${FONT}" xmlns="http://www.w3.org/2000/svg">`;
[lo,(lo+hi)/2,hi].forEach(v=>{const y=Y(v);s+=`<line x1="${padL}" y1="${y.toFixed(1)}" x2="${W-padR}" y2="${y.toFixed(1)}" stroke="#1c2c5c"/><text x="${padL-5}" y="${(y+3).toFixed(1)}" text-anchor="end" font-size="9" fill="#6f83bd">${v.toFixed(1)}%</text>`;});
const pathOf=a=>xs.map((_,i)=>a[i]==null?null:`${X(i).toFixed(1)},${Y(a[i]).toFixed(1)}`).filter(Boolean).join(" ");
if(prior&&prior.some(v=>v!=null))s+=`<polyline points="${pathOf(prior)}" fill="none" stroke="#64748b" stroke-width="1.6" stroke-dasharray="4 3"/>`;
s+=`<polyline points="${pathOf(live)}" fill="none" stroke="#f59e0b" stroke-width="2.4"/>`;
xs.forEach((xv,i)=>{if(i%Math.ceil(xs.length/8)===0)s+=`<text x="${X(i).toFixed(1)}" y="${H-8}" text-anchor="middle" font-size="8.5" fill="#6f83bd">${xv}</text>`;});
s+=`<rect x="${W-120}" y="10" width="10" height="3" fill="#f59e0b"/><text x="${W-105}" y="14" font-size="9" fill="#9db0e8">live</text><rect x="${W-65}" y="10" width="10" height="3" fill="#64748b"/><text x="${W-50}" y="14" font-size="9" fill="#9db0e8">prior</text>`;
s+=`<text x="${W/2}" y="${H-1}" text-anchor="middle" font-size="8.5" fill="#5c6f9f">${xlab}</text>`;
document.getElementById(elId).innerHTML=s+`</svg>`;}
line2d("skew",D.skew.strike,D.skew.live,D.skew.prior,"strike");
line2d("term",D.term.dte,D.term.live,D.term.prior,"days to expiry");
</script></body></html>"""


if __name__ == "__main__":
    main()
