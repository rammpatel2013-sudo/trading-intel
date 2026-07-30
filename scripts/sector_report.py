"""Sector lead/lag + fragility report — one self-contained HTML, pushed to Telegram.

Canonical generator + CLI for the sector report (see MEMORY ``sector-report``).
Mirrors ``scripts/cockpit_report.py``: layout defined once here, the HTML
template INLINED (a module string) so nothing lives in a separate asset file a
stray ``.gitignore`` rule could drop. ``trading_intel.reports.build_sector``
loads this module's ``build()`` so the MCP ``generate_sector_report`` tool
produces the identical file.

Reads the CVForge-fed ``greeks_snapshots`` (SPDRs, source ``cvforge``), the
``sector_corr_snapshots`` correlation regime, and free yfinance history — NO
Convex calls (rule 1). The brain (ranking + LEAP flags) is the pure
``market.sector_scan``. Descriptor only (FlashAlpha rule 4): lead/lag + flags,
never a trade signal — LEAP selection stays in validated ``strategies/``.

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
    --amb:#f4b942; --blu:#5aa9e6; --txt:#e9eef0; --mut:#6c777d;
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

  .cand{margin-top:2px}
  .cand .item{background:var(--card2);border:1px solid #22333a;border-radius:11px;padding:10px 12px;margin-bottom:8px}
  .cand .hd{display:flex;align-items:center;justify-content:space-between;margin-bottom:6px}
  .cand .hd .s{font-weight:700}
  .cand .r{font-size:11px;color:#9aa4a9;line-height:1.5;margin:1px 0}
  .cand .r .k{color:var(--grn-dim);font-weight:700}
  .cand .r.ag .k{color:var(--red-dim)}
  .foot{margin-top:12px;padding:0 4px}
  .foot p{font-size:10.5px;color:#5a656a;line-height:1.6;margin-bottom:5px}
  .empty{font-size:12px;color:var(--mut);padding:8px 2px;line-height:1.5}
</style>
</head>
<body>
<div class="app">
  <div class="top"><h1 id="title">Sector Lead / Lag</h1><div class="as" id="asof"></div></div>
  <div id="body"></div>
  <div class="foot">
    <p id="footmeta"></p>
    <p><b>Descriptor only.</b> Lead/lag &amp; fragility read + LEAP-setup FLAGS with rationale — not a trade signal. Long gamma = dealers dampen (stable); short gamma = dealers amplify (fragile). Correlation is the go / no-go gate: low = single-sector bets diversify; high = it's index beta. Actual LEAP selection stays in the validated strategy layer.</p>
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

function gateCard(){
  const c=P.correlation||{}, open=c.gate_open;
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
    </div></div>`;
}
function internalsCard(){
  const i=P.internals||{};
  if(i.n==null||!i.n) return "";
  const col=i.healthy==null?"amb":(i.healthy?"grn":"red");
  return `<div class="card"><div class="lbl">Market internals · sector breadth vs index</div>
    <div class="gate"><div>
      <div class="big">${i.n_up}/${i.n} sectors up <span class="mutv" style="font-weight:400">· SPY ${i.index_up==null?"—":(i.index_up?"up":"down")}</span></div>
      <div class="sub">${esc(i.divergence||"")}</div>
    </div><div class="pill ${col}">${i.healthy==null?"MIXED":(i.healthy?"HEALTHY":"FRAGILE")}</div></div></div>`;
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
  $("body").innerHTML = gateCard()+internalsCard()+banner+tableCard()+candCard();
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
