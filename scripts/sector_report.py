"""Sector lead/lag + fragility report — one self-contained HTML, pushed to Telegram.

Canonical generator + CLI for the sector report (see MEMORY ``sector-report``).
Mirrors ``scripts/cockpit_report.py``: layout defined once here, the HTML
template INLINED (a module string) so nothing lives in a separate asset file a
stray ``.gitignore`` rule could drop. ``trading_intel.reports.build_sector``
loads this module's ``build()`` so the MCP ``generate_sector_report`` tool
produces the identical file.

Reads the CVForge-fed ``greeks_snapshots`` (SPDRs, source ``cvforge``) + the
``sector_snapshots`` skew/walls, and computes the correlation / dispersion /
breadth TRENDS live from free yfinance history — NO Convex calls (rule 1). The
brain (ranking + LEAP flags) is the pure ``market.sector_scan``. Descriptor only
(FlashAlpha rule 4): lead/lag + flags, never a trade signal — LEAP selection
stays in validated ``strategies/``.

Run:
    python scripts/sector_report.py            # build + push to Telegram
    python scripts/sector_report.py --no-push  # build only
"""
from __future__ import annotations

import json
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

_DEFAULT_OUT = Path("reports") / "sector.html"

_TEMPLATE_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>Sector Lead / Lag · Fragility</title>
<style>
  :root{
    --bg:#08090a; --card:#111618; --card2:#0d1214; --edge:#1c2427;
    --grn:#2fe0a6; --grn-dim:#1c8e6c; --red:#ff5d6a; --red-dim:#9e3540;
    --amb:#f4b942; --blu:#5aa9e6; --vio:#8a7fe0; --txt:#e9eef0; --mut:#6c777d;
    --mono:"SF Mono",ui-monospace,"Roboto Mono",Menlo,monospace;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--bg);color:var(--txt);
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
    -webkit-font-smoothing:antialiased;display:flex;justify-content:center;padding:16px 12px 40px}
  .app{width:100%;max-width:520px}
  .num{font-family:var(--mono);font-variant-numeric:tabular-nums;letter-spacing:-.02em}
  .top{display:flex;align-items:baseline;justify-content:space-between;padding:2px 4px 12px}
  .top h1{font-size:17px;font-weight:700;letter-spacing:.2px}
  .top .as{font-size:11px;color:var(--mut)}

  .card{background:var(--card);border:1px solid var(--edge);border-radius:16px;padding:14px 15px;margin-bottom:10px}
  .lbl{font-size:10.5px;letter-spacing:1.4px;color:var(--mut);font-weight:700;text-transform:uppercase;margin-bottom:9px}
  .gate{display:flex;align-items:center;justify-content:space-between;gap:12px}
  .gate .big{font-size:16px;font-weight:700}
  .gate .sub{font-size:11.5px;color:#9aa4a9;margin-top:2px;line-height:1.4}
  .pill{font-size:10px;font-weight:700;letter-spacing:.6px;padding:5px 10px;border-radius:20px;white-space:nowrap}
  .pill.grn{background:rgba(47,224,166,.13);color:var(--grn)}
  .pill.red{background:rgba(255,93,106,.13);color:var(--red)}
  .pill.amb{background:rgba(244,185,66,.14);color:var(--amb)}
  .metrics{display:flex;gap:14px;margin-top:12px;padding-top:11px;border-top:1px solid var(--edge)}
  .metrics div{font-size:11px;color:var(--mut)}
  .metrics b{display:block;font-size:14px;color:var(--txt);margin-top:3px;font-family:var(--mono)}

  .sparks{display:flex;gap:12px;margin-top:12px;padding-top:11px;border-top:1px solid var(--edge)}
  .sparks>div{flex:1;display:flex;flex-direction:column;gap:3px}
  .sparks span{font-size:9px;letter-spacing:.5px;color:var(--mut);text-transform:uppercase}

  table{width:100%;border-collapse:collapse;margin-top:2px}
  th{font-size:9.5px;letter-spacing:.5px;color:var(--mut);font-weight:700;text-align:right;padding:4px 5px;border-bottom:1px solid var(--edge)}
  th.l,td.l{text-align:left}
  td{font-size:12px;padding:7px 5px;border-bottom:1px solid #131a1c;font-family:var(--mono)}
  tr:last-child td{border-bottom:none}
  .sym{font-weight:700;font-family:-apple-system,sans-serif}
  .tag{font-size:9px;font-weight:700;letter-spacing:.4px;padding:2px 6px;border-radius:6px}
  .tag.stable{background:rgba(47,224,166,.13);color:var(--grn)}
  .tag.fragile{background:rgba(255,93,106,.13);color:var(--red)}
  .tag.na{background:#161d20;color:var(--mut)}
  .setup{font-size:9px;font-weight:700;letter-spacing:.4px;padding:2px 7px;border-radius:20px}
  .setup.candidate{background:rgba(47,224,166,.15);color:var(--grn)}
  .setup.watch{background:rgba(90,169,230,.14);color:var(--blu)}
  .setup.avoid{background:rgba(255,93,106,.14);color:var(--red)}
  .setup.na{background:#161d20;color:var(--mut)}
  .pos{color:var(--grn)}.neg{color:var(--red)}.mutv{color:var(--mut)}

  .how ol{margin:2px 0 8px 0;padding-left:18px}
  .how li{font-size:11.5px;color:#c7d0d3;line-height:1.55;margin-bottom:7px}
  .how li b{color:var(--txt)}

  .skrow{padding:9px 2px;border-bottom:1px solid #131a1c}
  .skrow:last-of-type{border-bottom:none}
  .skh{display:flex;align-items:center;gap:10px;font-size:13px;flex-wrap:wrap}
  .skh .sym{font-weight:700}
  .skmid{margin:5px 0 4px}
  .skf{font-size:11px;color:#9aa4a9;font-family:var(--mono)}

  .cand{margin-top:2px}
  .cand .item{background:var(--card2);border:1px solid #22333a;border-radius:11px;padding:10px 12px;margin-bottom:8px}
  .cand .hd{display:flex;align-items:center;justify-content:space-between;margin-bottom:6px}
  .cand .hd .s{font-weight:700}
  .cand .r{font-size:11px;color:#9aa4a9;line-height:1.5;margin:1px 0}
  .cand .r .k{color:var(--grn-dim);font-weight:700}
  .cand .r.ag .k{color:var(--red-dim)}
  .foot{margin-top:12px;padding:0 4px}
  .foot p{font-size:10.5px;color:#5a656a;line-height:1.6;margin-bottom:5px}
  .empty{font-size:11.5px;color:var(--mut);padding:8px 2px;line-height:1.5}
</style>
</head>
<body>
<div class="app">
  <div class="top"><h1 id="title">Sector Lead / Lag</h1><div class="as" id="asof"></div></div>
  <div id="body"></div>
  <div class="foot">
    <p id="footmeta"></p>
    <p><b>Descriptor only.</b> Lead/lag &amp; fragility read + LEAP-setup FLAGS with rationale — not a trade signal. Long gamma = dealers dampen (stable); short gamma = dealers amplify (fragile). Correlation is the go / no-go gate: low = single-sector bets diversify; high = index beta. Actual LEAP selection stays in the validated strategy layer.</p>
  </div>
</div>
<script>
const P = __SECTOR_DATA__;
const $ = id => document.getElementById(id);
const pct=(x,d=1)=>x==null||!isFinite(x)?"—":(x*100).toFixed(d)+"%";
const spct=(x,d=1)=>x==null||!isFinite(x)?"—":(x>=0?"+":"−")+Math.abs(x*100).toFixed(d)+"%";
const abbr=(x,d=1)=>{if(x==null||!isFinite(x))return"—";const s=x<0?"−":"",a=Math.abs(x);
  if(a>=1e9)return s+(a/1e9).toFixed(d)+"B";if(a>=1e6)return s+(a/1e6).toFixed(d)+"M";if(a>=1e3)return s+(a/1e3).toFixed(d)+"K";return s+a.toFixed(d);};
const esc=s=>String(s==null?"":s).replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));

function spark(vals,{w=88,h=22,color="#5aa9e6",fill=false}={}){
  const v=(vals||[]).map(x=>x==null||!isFinite(x)?null:+x);
  const ok=v.filter(x=>x!=null);
  if(ok.length<2) return `<span class="mutv" style="font-size:9.5px">building…</span>`;
  const mn=Math.min(...ok),mx=Math.max(...ok),rg=(mx-mn)||1,n=v.length;
  const xy=v.map((x,i)=> x==null?null:[i/(n-1)*w, h-2-((x-mn)/rg)*(h-4)]).filter(Boolean);
  const d=xy.map((p,i)=>(i?"L":"M")+p[0].toFixed(1)+" "+p[1].toFixed(1)).join(" ");
  const last=xy[xy.length-1];
  const area=fill?`<path d="${d} L ${last[0].toFixed(1)} ${h} L ${xy[0][0].toFixed(1)} ${h} Z" fill="${color}" opacity="0.10"/>`:"";
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" style="vertical-align:middle">${area}<path d="${d}" fill="none" stroke="${color}" stroke-width="1.5"/><circle cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}" r="1.9" fill="${color}"/></svg>`;
}
function shiftLabel(sh){
  if(sh==null) return `<span class="mutv">— building</span>`;
  const v=Math.abs(sh*100).toFixed(2);
  if(sh<-0.0005) return `<span class="pos">▼ ${v} → calls</span>`;
  if(sh>0.0005) return `<span class="neg">▲ ${v} → puts</span>`;
  return `<span class="mutv">flat</span>`;
}

function gateCard(){
  const c=P.correlation||{}, t=P.trends||{}, open=c.gate_open;
  const lbl=(c.regime_label||"n/a").split(" — ")[0].toUpperCase();
  const cls=open?"grn":(lbl==="HIGH"?"red":"amb");
  const a21=(c.avg_corr||{})["21d"], a63=(c.avg_corr||{})["63d"];
  return `<div class="card"><div class="lbl">Correlation gate · single-sector bets</div>
    <div class="gate"><div>
      <div class="big">${open?"OPEN — dispersion":"CAUTION — "+lbl.toLowerCase()+" correlation"}</div>
      <div class="sub">${open?"sectors decoupled — a single-sector LEAP actually isolates that sector":"sectors move together — a single-sector LEAP is mostly index beta"}</div>
    </div><div class="pill ${cls}">${open?"GO":"GATE"}</div></div>
    <div class="metrics">
      <div>AVG CORR 21D<b>${a21==null?"—":a21.toFixed(2)}</b></div>
      <div>AVG CORR 63D<b>${a63==null?"—":a63.toFixed(2)}</b></div>
      <div>DISPERSION<b>${c.dispersion==null?"—":(c.dispersion*100).toFixed(2)}</b></div>
    </div>
    <div class="sparks">
      <div><span>corr 21d</span>${spark(t.corr21,{color:"#5aa9e6"})}</div>
      <div><span>corr 63d</span>${spark(t.corr63,{color:"#8a7fe0"})}</div>
      <div><span>dispersion</span>${spark(t.dispersion,{color:"#f4b942"})}</div>
    </div></div>`;
}
function internalsCard(){
  const i=P.internals||{};
  if(i.n==null||!i.n) return "";
  const col=i.healthy==null?"amb":(i.healthy?"grn":"red");
  const spk=(i.trend&&i.trend.filter(x=>x!=null).length)?`<div class="sparks"><div><span>% sectors up · ${i.trend.filter(x=>x!=null).length}d</span>${spark(i.trend,{color:(col==='red'?'#ff5d6a':col==='grn'?'#2fe0a6':'#f4b942'),fill:true})}</div></div>`:"";
  return `<div class="card"><div class="lbl">Market internals · sector breadth vs index</div>
    <div class="gate"><div>
      <div class="big">${i.n_up}/${i.n} sectors up <span class="mutv" style="font-weight:400">· SPY ${i.index_up==null?"—":(i.index_up?"up":"down")}</span></div>
      <div class="sub">${esc(i.divergence||"")}</div>
    </div><div class="pill ${col}">${i.healthy==null?"MIXED":(i.healthy?"HEALTHY":"FRAGILE")}</div></div>${spk}</div>`;
}
function howToCard(){
  const gate=(P.correlation||{}).gate_open;
  return `<div class="card how"><div class="lbl">How to read this → finding a LEAP-long</div>
    <ol>
      <li><b>Gate first.</b> The correlation gate must be OPEN (low / dispersion) or a single-sector LEAP is really just index beta. Right now: <b class="${gate?'pos':'neg'}">${gate?'OPEN':'gated'}</b>.</li>
      <li><b>Pick from the leaders.</b> Long-gamma (stable) sector, above its gamma flip (cushion under spot), positive 21-day momentum — the top of the ranking.</li>
      <li><b>Buy cheap vega.</b> A LEAP is long vega, so favour a LOW ATM-IV percentile — you want implied vol cheap at entry.</li>
      <li><b>The skew shift is the trigger.</b> Watch the 25Δ RR rotate from put-rich toward the call side (▼ falling) — that's real money starting to bid calls, early accumulation, the LEAP-call window. Rising RR (▲, fear bidding puts) = wait.</li>
      <li><b>Confirm at the wall.</b> The call wall above spot is the target / resistance. If fixed-strike vol there is OFFERED the wall pins (favour call spreads into it); if it's BID the level is set to break (favour straight calls).</li>
    </ol>
    <div class="empty">These are descriptor flags, not a signal — size and pick the actual contract in your validated strategy layer.</div></div>`;
}
function ivCell(s){
  if(s.iv_pctile==null) return `<td>${s.atm_iv==null?"—":pct(s.atm_iv)}<span class="mutv"> ·—</span></td>`;
  const cls=s.iv_pctile<0.35?"pos":(s.iv_pctile>0.7?"neg":"mutv");
  return `<td>${pct(s.atm_iv)} <span class="${cls}">${Math.round(s.iv_pctile*100)}p</span></td>`;
}
function tableCard(){
  const rows=(P.sectors||[]).map(s=>{
    if(s.gamma_regime==null){
      return `<tr><td class="l">${s.rank||""}</td><td class="l sym">${esc(s.symbol)}</td>
        <td class="l"><span class="tag na">pending</span></td><td colspan="4" class="mutv" style="text-align:left">awaiting CVForge snapshot</td>
        <td><span class="setup na">n/a</span></td></tr>`;
    }
    const stab=s.stability||"na";
    const mom=s.ret_21d, momc=mom==null?"mutv":(mom>=0?"pos":"neg");
    const cush=s.gflip_cushion, cushc=cush==null?"mutv":(cush>=0?"pos":"neg");
    return `<tr>
      <td class="l">${s.rank||""}</td>
      <td class="l sym">${esc(s.symbol)}</td>
      <td class="l"><span class="tag ${stab}">${stab==="stable"?"long γ":stab==="fragile"?"short γ":"—"}</span></td>
      <td class="${cushc}">${spct(cush)}</td>
      ${ivCell(s)}
      <td class="${momc}">${spct(mom)}</td>
      <td>${s.lead_score==null?"—":s.lead_score.toFixed(2)}</td>
      <td><span class="setup ${s.leap?s.leap.setup:'na'}">${s.leap?s.leap.setup:"n/a"}</span></td>
    </tr>`;
  }).join("");
  return `<div class="card"><div class="lbl">Lead → lag · fragility ranking</div>
    <table><thead><tr>
      <th class="l">#</th><th class="l">ETF</th><th class="l">γ regime</th>
      <th>flip cush</th><th>ATM IV·pct</th><th>21d</th><th>score</th><th>LEAP</th>
    </tr></thead><tbody>${rows}</tbody></table></div>`;
}
function wallsCard(){
  const priced=(P.sectors||[]).filter(s=>s.gamma_regime!=null);
  if(!priced.length) return "";
  const arr=[...priced].sort((a,b)=>{const x=a.rr25_shift,y=b.rr25_shift;
    if(x==null&&y==null)return 0; if(x==null)return 1; if(y==null)return -1; return x-y;});
  const wdist=(w,spot)=> w==null||!spot?"":` <span class="mutv">${(w/spot-1>=0?"+":"−")}${Math.abs((w/spot-1)*100).toFixed(1)}%</span>`;
  const items=arr.map(s=>{
    const rr=s.rr25, rrt=rr==null?"—":(rr>=0?"+":"−")+Math.abs(rr*100).toFixed(2), rrc=rr==null?"mutv":(rr>=0?"neg":"pos");
    const fp=s.footprint||{};
    const fr=fp.pending?'<span class="mutv">fx: 2nd day</span>':(!fp.read?'':
      fp.read.indexOf("HOLD")>=0?'<span class="pos">fx: offered · hold</span>':
      fp.read.indexOf("BREAK")>=0?'<span class="neg">fx: bid · break</span>':'<span class="mutv">fx: mixed</span>');
    const skcol=(s.rr25_shift!=null&&s.rr25_shift<0)?"#2fe0a6":(s.rr25_shift!=null&&s.rr25_shift>0)?"#ff5d6a":"#6c777d";
    return `<div class="skrow">
      <div class="skh"><span class="sym">${esc(s.symbol)}</span>
        <span class="${rrc}" style="font-family:var(--mono)">RR ${rrt}</span>
        ${shiftLabel(s.rr25_shift)}</div>
      <div class="skmid">${spark(s.rr25_trend,{w:130,h:24,color:skcol})}</div>
      <div class="skf"><span class="mutv">walls</span> ${s.call_wall==null?"—":Math.round(s.call_wall)}${wdist(s.call_wall,s.spot)} <span class="mutv">/</span> ${s.put_wall==null?"—":Math.round(s.put_wall)}${wdist(s.put_wall,s.spot)} · ${fr}</div>
    </div>`;
  }).join("");
  return `<div class="card"><div class="lbl">Skew shift · put ↔ call rotation <span style="color:#3a4448;font-weight:400;letter-spacing:0;text-transform:none">· most call-side first</span></div>
    ${items}
    <div class="empty" style="padding-top:8px">25Δ RR = put IV − call IV (+ puts richer / fear). A FALLING RR (▼ → calls) = demand rotating to the call side = the bullish LEAP-call tell; rising (▲ → puts) = defensive. Walls = peak gamma-OI strike. fx = fixed-strike vol offered (hold) / bid (break); the shift + fx fill on the 2nd day of history.</div></div>`;
}
function candCard(){
  const cands=(P.sectors||[]).filter(s=>s.leap&&(s.leap.setup==="candidate"));
  if(!cands.length) return `<div class="card"><div class="lbl">LEAP-long candidates</div>
    <div class="empty">No sector clears the candidate bar right now (needs a stable/long-gamma tape, ≥2 supporting reads, and the dispersion gate open). Watch the table above.</div></div>`;
  const items=cands.map(s=>{
    const fors=(s.leap.for||[]).map(r=>`<div class="r"><span class="k">+</span> ${esc(r)}</div>`).join("");
    const ags=(s.leap.against||[]).map(r=>`<div class="r ag"><span class="k">−</span> ${esc(r)}</div>`).join("");
    return `<div class="item"><div class="hd"><div class="s">${esc(s.symbol)} <span class="mutv" style="font-size:11px;font-weight:400">#${s.rank}</span></div>
      <span class="setup candidate">candidate</span></div>${fors}${ags}</div>`;
  }).join("");
  return `<div class="card"><div class="lbl">LEAP-long candidates · context, not a signal</div><div class="cand">${items}</div></div>`;
}
function render(){
  $("asof").textContent = "as of "+String(P.as_of||"").replace("T"," ");
  const lead=(P.leaders||[]).join(" · ")||"—", lag=(P.laggards||[]).join(" · ")||"—";
  const banner=`<div class="card" style="display:flex;gap:14px"><div style="flex:1"><div class="lbl" style="margin-bottom:5px">Leaders</div><div class="pos" style="font-weight:700">${esc(lead)}</div></div>
    <div style="flex:1"><div class="lbl" style="margin-bottom:5px">Laggards</div><div class="neg" style="font-weight:700">${esc(lag)}</div></div></div>`;
  $("body").innerHTML = gateCard()+internalsCard()+banner+howToCard()+tableCard()+wallsCard()+candCard();
  const m=P.meta||{};
  $("footmeta").textContent = `${m.n_priced||0}/${m.n_sectors||(P.sectors||[]).length} sectors priced · source ${m.source||"—"} · correlation as of ${(P.correlation||{}).as_of||"—"}`;
}
render();
</script>
</body>
</html>
"""


def _render_html(payload: dict, out_path: str | None) -> str:
    data = json.dumps(payload).replace("</", "<\\/")
    html = _TEMPLATE_HTML.replace("__SECTOR_DATA__", data)
    out = (Path(out_path) if out_path else _DEFAULT_OUT).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return str(out)


def build(*, out_path: str | None = None, settings: object = None, session: object = None) -> str:
    """Assemble the sector payload from the DB and bake it into one HTML file.

    Opens its own session unless one is passed. Returns the absolute path.
    """
    from trading_intel.api.sector import build_sector
    from trading_intel.config import get_settings

    settings = settings or get_settings()
    if session is not None:
        payload = build_sector(session, settings)
    else:
        from trading_intel.memory.db import make_session_factory

        with make_session_factory(settings)() as s:
            payload = build_sector(s, settings)
    return _render_html(payload, out_path)


def run(*, push: bool = True, settings: object = None) -> str:
    """Build the sector report and (optionally) push it to Telegram. Returns the path."""
    from trading_intel.config import get_settings

    settings = settings or get_settings()
    path = build(settings=settings)
    if push:
        from trading_intel.clients.telegram import TelegramClient

        sent = TelegramClient(settings).send_document(
            path, caption="Sector lead/lag + fragility"
        )
        log.info("sector_report.pushed", path=path, telegram_sent=sent)
    return path


def main() -> None:
    """Manual/scheduled entrypoint: build the sector report and push it to Telegram."""
    import argparse

    parser = argparse.ArgumentParser(description="Build the sector lead/lag + fragility report.")
    parser.add_argument("--no-push", action="store_true", help="build only; do not push to Telegram")
    args = parser.parse_args()

    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    path = run(push=not args.no_push)
    print(f"sector report written: {path}")


if __name__ == "__main__":
    main()
