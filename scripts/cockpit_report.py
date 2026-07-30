"""Cockpit report — SPX + SPY dealer positioning → one self-contained HTML,
pushed to Telegram.

Canonical generator + CLI for the dealer-positioning cockpit (see MEMORY
``cockpit-report``). Mirrors the other ``scripts/*_report.py`` generators: the
layout lives here once, the HTML template is INLINED (a module string) exactly
like ``eod_vol_report.py`` / ``vol_surface_report.py`` build their HTML — so
nothing lives in a separate asset file that a stray ``.gitignore`` rule could
silently drop. ``trading_intel.reports.build_cockpit`` loads this module's
``build()`` so the MCP ``generate_cockpit_report`` tool produces the identical
file.

No running service: a scheduled (or on-demand) job renders the latest
Convex-fed NAS snapshot into a single file and pushes it to the Telegram bot,
so you open it from Telegram like your other reports. Both symbols are baked in,
so the SPX/SPY toggle works offline. Reads the Convex-fed DB via
``api.positioning.build_positioning`` — ZERO added vendor calls. Descriptor
only (FlashAlpha rule 4).

Run:
    python scripts/cockpit_report.py            # build + push to Telegram
    python scripts/cockpit_report.py --no-push  # build only
"""
from __future__ import annotations

import json
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

_SYMBOLS: tuple[str, ...] = ("SPX", "SPY", "QQQ")  # fallback; real default = config INDEX_ROOTS
_DEFAULT_OUT = Path("reports") / "cockpit.html"

# Self-contained mobile cockpit. ``__COCKPIT_DATA__`` is replaced with the baked
# JSON payloads (both symbols) at build time — the page then runs fully offline.
_TEMPLATE_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>Dealer Positioning · Live</title>
<style>
  :root{
    --bg:#08090a; --card:#111618; --card2:#0d1214; --edge:#1c2427;
    --grn:#2fe0a6; --grn-dim:#1c8e6c; --red:#ff5d6a; --red-dim:#9e3540;
    --amb:#f4b942; --txt:#e9eef0; --mut:#6c777d;
    --mono:"SF Mono",ui-monospace,"Roboto Mono",Menlo,monospace;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--bg);color:var(--txt);
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
    -webkit-font-smoothing:antialiased;display:flex;justify-content:center;padding:16px 12px 40px}
  .app{width:100%;max-width:404px}
  .num{font-family:var(--mono);font-variant-numeric:tabular-nums;letter-spacing:-.02em}

  .top{display:flex;align-items:center;justify-content:space-between;padding:4px 4px 12px}
  .toggle{display:flex;gap:6px}
  .toggle button{background:#12181a;border:1px solid var(--edge);color:var(--mut);
    font:600 13px/1 -apple-system,sans-serif;letter-spacing:1px;padding:8px 14px;border-radius:10px;cursor:pointer}
  .toggle button.on{background:rgba(47,224,166,.12);border-color:#2b5;color:var(--grn)}
  .status{display:flex;align-items:center;gap:7px;font-size:11px;color:var(--mut);text-align:right}
  .dot{width:7px;height:7px;border-radius:50%;background:var(--grn);box-shadow:0 0 0 0 rgba(47,224,166,.6);animation:pulse 2s infinite}
  .dot.stale{background:var(--amb);animation:none}
  .dot.err{background:var(--red);animation:none}
  @keyframes pulse{0%{box-shadow:0 0 0 0 rgba(47,224,166,.5)}70%{box-shadow:0 0 0 7px rgba(47,224,166,0)}100%{box-shadow:0 0 0 0 rgba(47,224,166,0)}}

  .card{background:var(--card);border:1px solid var(--edge);border-radius:16px;padding:15px 16px;margin-bottom:10px}
  .lbl{font-size:10.5px;letter-spacing:1.6px;color:var(--mut);font-weight:700;text-transform:uppercase}
  .pill{font-size:10px;font-weight:700;letter-spacing:.6px;padding:4px 8px;border-radius:20px;display:inline-flex;gap:4px}
  .pill.red{background:rgba(255,93,106,.13);color:var(--red)}
  .pill.grn{background:rgba(47,224,166,.12);color:var(--grn)}

  .regime .row1{display:flex;justify-content:space-between;align-items:center;margin-bottom:9px}
  .regime .big{font-size:25px;font-weight:700;letter-spacing:.2px}
  .regime .sub{font-size:12.5px;color:#9aa4a9;margin-top:1px}
  .meta{display:flex;gap:16px;margin-top:13px;padding-top:12px;border-top:1px solid var(--edge)}
  .meta div{font-size:11px;color:var(--mut)}
  .meta b{display:block;font-size:14px;color:var(--txt);margin-top:3px;font-family:var(--mono)}

  .emv .r{display:flex;justify-content:space-between;align-items:flex-end;margin:10px 0 4px}
  .emv .pct{font-size:29px;font-weight:700}
  .emv .dol{font-size:16px;color:#aeb8bd;font-family:var(--mono)}
  .track{position:relative;height:5px;border-radius:3px;margin:20px 0 7px;
    background:linear-gradient(90deg,var(--red-dim),#2a3236 46%,#2a3236 54%,var(--grn-dim))}
  .track .mk{position:absolute;top:50%;width:12px;height:12px;border-radius:50%;background:#e9eef0;
    border:2px solid #08090a;transform:translate(-50%,-50%);box-shadow:0 0 0 1px #2fe0a6}
  .ends{display:flex;justify-content:space-between;font-size:11px}
  .ends .lo{color:var(--red)}.ends .hi{color:var(--grn)}.ends .sp{color:var(--mut);text-align:center}
  .ends b{display:block;font-family:var(--mono);font-size:12.5px;margin-top:2px;color:var(--txt)}
  .ends .lo b{color:var(--red)}.ends .hi b{color:var(--grn)}
  .fine{font-size:11px;color:var(--mut);margin-top:12px;line-height:1.5}

  .two{display:flex;gap:12px;margin:12px 0 4px}
  .two>div{flex:1}
  .k{font-size:11px;color:var(--mut);margin-bottom:5px}
  .v{font-size:22px;font-weight:700;font-family:var(--mono)}
  .v.red{color:var(--red)}.v.grn{color:var(--grn)}
  .u{font-size:10.5px;color:var(--mut);margin-top:3px}
  .u.g{color:var(--grn-dim)}.u.r{color:var(--red-dim)}
  .brk{margin-top:15px;padding-top:13px;border-top:1px solid var(--edge)}
  .brk .h{display:flex;justify-content:space-between;align-items:center;margin-bottom:11px}
  .brk .h .t{font-size:10.5px;letter-spacing:1.4px;color:var(--mut);font-weight:700}
  .brk .h .tot{font-size:11px;color:var(--mut);font-family:var(--mono)}
  .bar{display:flex;align-items:center;gap:10px;margin:8px 0}
  .bar .nm{width:60px;font-size:11px;color:#9aa4a9;flex-shrink:0}
  .bar .tr{flex:1;height:7px;background:#0c1113;border-radius:4px;position:relative;overflow:hidden}
  .bar .fill{position:absolute;top:0;height:100%;border-radius:4px;opacity:.9}
  .bar .vl{width:74px;text-align:right;font-family:var(--mono);font-size:12px;flex-shrink:0}
  .bar .vl.red{color:var(--red)}.bar .vl.grn{color:var(--grn)}
  .lean{margin-top:14px;padding:11px 12px;background:var(--card2);border:1px solid #26454a;border-radius:11px;
    display:flex;justify-content:space-between;align-items:center;gap:10px}
  .lean .lt{font-size:11px;color:#9aa4a9;line-height:1.45}
  .lean .lt b{color:var(--txt)}
  .lean .rt{font-size:12px;color:var(--txt);font-family:var(--mono);white-space:nowrap}

  .pcbar{position:relative;height:8px;border-radius:5px;margin:12px 0 8px;overflow:hidden;display:flex}
  .pcbar .cf{background:var(--grn-dim)}.pcbar .pf{background:var(--red-dim)}
  .pcrow{display:flex;justify-content:space-between;font-size:11px}
  .pcrow .c{color:var(--grn)}.pcrow .p{color:var(--red)}
  .pcrow b{font-family:var(--mono);color:var(--txt);font-weight:600}

  .sk .grid{display:flex;gap:9px;margin:12px 0 2px}
  .sk .cell{flex:1;background:var(--card2);border:1px solid var(--edge);border-radius:11px;padding:10px 11px}
  .sk .cell .kk{font-size:9.5px;color:var(--mut);letter-spacing:.5px;margin-bottom:6px}
  .sk .cell .vv{font-size:19px;font-weight:700;font-family:var(--mono)}
  .sk .cell .vv.red{color:var(--red)}.sk .cell .vv.grn{color:var(--grn)}
  .sk .cell .vv.na{color:var(--mut);font-size:14px}
  .sk .cell .ss{font-size:10px;color:var(--mut);margin-top:4px}

  .foot{margin-top:14px;padding:0 4px}
  .foot p{font-size:10.5px;color:#5a656a;line-height:1.6;margin-bottom:6px}
  .foot .ok{color:var(--grn-dim)}
  #err{display:none;background:rgba(255,93,106,.08);border:1px solid var(--red-dim);color:#ffb3ba;
    border-radius:12px;padding:12px 14px;font-size:12px;margin-bottom:10px}
</style>
</head>
<body>
<div class="app">
  <div class="top">
    <div class="toggle" id="toggle"></div>
    <div class="status"><span class="dot" id="dot"></span><span id="upd">connecting…</span></div>
  </div>
  <div id="err"></div>
  <div id="cards"></div>
  <div class="foot">
    <p><b class="ok">● Snapshot</b> from the Convex-fed DB (near-live at the scheduler cadence) — generated report, no live service, no added Convex calls.</p>
    <p id="footmeta"></p>
  </div>
</div>
<script>
const PAYLOADS = __COCKPIT_DATA__;
const SYMBOLS = Object.keys(PAYLOADS);
let current = SYMBOLS[0] || "SPX";

const $ = id => document.getElementById(id);
const fmtC = (x,d=2) => x==null||!isFinite(x) ? "n/a" : Number(x).toLocaleString(undefined,{minimumFractionDigits:d,maximumFractionDigits:d});
function abbr(x,d=1){ if(x==null||!isFinite(x)) return "n/a";
  const s=x<0?"−":"", a=Math.abs(x);
  if(a>=1e9) return s+(a/1e9).toFixed(d)+"B";
  if(a>=1e6) return s+(a/1e6).toFixed(d)+"M";
  if(a>=1e3) return s+(a/1e3).toFixed(d)+"K";
  return s+a.toFixed(d); }
const pct = (x,d=2) => x==null||!isFinite(x) ? "n/a" : (x*100).toFixed(d)+"%";
const signPct = (x,d=2) => x==null||!isFinite(x) ? "n/a" : (x>=0?"+":"−")+Math.abs(x*100).toFixed(d)+"%";
const vols = (x,d=2) => x==null||!isFinite(x) ? null : (x>=0?"+":"−")+Math.abs(x*100).toFixed(d);

function buildToggle(){
  $("toggle").innerHTML = SYMBOLS.map(s=>`<button data-s="${s}" class="${s===current?'on':''}">${s}</button>`).join("");
  $("toggle").querySelectorAll("button").forEach(b=>b.onclick=()=>{current=b.dataset.s;buildToggle();show();});
}

function regimeCard(p){
  const r=p.regime, short=r.amplifying;
  const label = r.label ? r.label.toUpperCase() : "REGIME N/A";
  const col = short===null? "var(--mut)" : short? "var(--red)":"var(--grn)";
  const pill = short===null? "" : short? `<div class="pill red">▼ BELOW FLIP</div>`:`<div class="pill grn">▲ ABOVE FLIP</div>`;
  return `<div class="card regime" style="border-color:${short? 'rgba(255,93,106,.28)':'rgba(47,224,166,.24)'}">
    <div class="row1"><div>
      <div class="big" style="color:${col}">${label}</div>
      <div class="sub">${short===null?'':short?'dealers amplify the move · spot below flip':'dealers dampen the move · spot above flip'}</div>
    </div>${pill}</div>
    <div class="meta">
      <div>SPOT<b>${fmtC(p.spot,2)}</b></div>
      <div>GAMMA FLIP<b>${r.gex_flip==null?'n/a':fmtC(r.gex_flip,2)}</b></div>
      <div>DIST TO FLIP<b style="color:${col}">${signPct(r.dist_to_flip)}</b></div>
    </div></div>`;
}

function emCard(p){
  const e=p.expected_move; if(!e) return "";
  const mk = e.upper>e.lower ? Math.max(0,Math.min(100,(p.spot-e.lower)/(e.upper-e.lower)*100)) : 50;
  return `<div class="card emv">
    <div class="lbl">Expected move · ${e.dte===0?'0DTE':e.dte+'-day'}</div>
    <div class="r"><div class="pct num">${pct(e.pct)}</div><div class="dol">±$${fmtC(e.dollar,2)}</div></div>
    <div class="track"><div class="mk" style="left:${mk.toFixed(1)}%"></div></div>
    <div class="ends">
      <div class="lo">MIN<b>${fmtC(e.lower,2)}</b></div>
      <div class="sp">SPOT<b>${fmtC(p.spot,2)}</b></div>
      <div class="hi">MAX<b>${fmtC(e.upper,2)}</b></div>
    </div>
    <div class="fine">ATM straddle · strike ${fmtC(e.atm_strike,0)} · ATM IV ${pct(e.atm_iv,1)}</div></div>`;
}

function dpCard(p){
  const g=p.gex, d=p.dex;
  const bmax = Math.max(...g.by_dte.map(b=>Math.abs(b.gex)),1e-9);
  const bars = g.by_dte.map(b=>{
    const w=Math.abs(b.gex)/bmax*50, neg=b.gex<0;
    const fill = neg? `right:50%;background:var(--red)`:`left:50%;background:var(--grn)`;
    return `<div class="bar"><div class="nm">${b.bucket} DTE</div>
      <div class="tr"><div class="fill" style="${fill};width:${w.toFixed(1)}%"></div></div>
      <div class="vl ${neg?'red':'grn'}">${abbr(b.gex)}</div></div>`;}).join("");
  const gcol=(g.total??0)<0?'red':'grn', dcol=(d.total??0)<0?'red':'grn';
  const flipTxt = d.flip==null? `<span style="color:var(--amb)">pending persist</span>` : `${fmtC(d.flip,2)} <span style="color:var(--mut)">(${d.side||''} ${d.dist_to_flip==null?'':signPct(d.dist_to_flip)})</span>`;
  return `<div class="card">
    <div class="lbl">Dealer positioning</div>
    <div class="two">
      <div><div class="k">Net GEX <span style="color:#3a4448">· term</span></div>
        <div class="v ${gcol} num">${abbr(g.total)}</div>
        <div class="u ${gcol==='red'?'r':'g'}">${gcol==='red'?'short gamma':'long gamma'} · near ${abbr(g.near_tenor)}</div></div>
      <div><div class="k">Net DEX</div>
        <div class="v ${dcol} num">${abbr(d.total)}</div>
        <div class="u ${dcol==='red'?'r':'g'}">${d.lean||''}</div></div>
    </div>
    <div class="brk"><div class="h"><div class="t">GEX BREAKDOWN BY DTE</div>
      <div class="tot">term total <b style="color:var(--${gcol})">${abbr(g.total)}</b></div></div>${bars}</div>
    <div class="lean"><div class="lt">Delta flip <span style="color:var(--mut)">(zero-DEX)</span>:</div>
      <div class="rt">${flipTxt}</div></div></div>`;
}

function flowCard(p){
  const f=p.flow;
  if(f.pending){
    return `<div class="card"><div style="display:flex;justify-content:space-between;align-items:center">
      <div class="lbl">Put / Call volume</div><div style="font-size:11px;color:var(--amb);font-weight:700;letter-spacing:.5px">PENDING</div></div>
      <div style="font-size:11px;color:var(--mut);margin-top:9px;line-height:1.5">Fills once <code>intraday_flow</code> is re-enabled for this index (set <code>INTRADAY_SYMBOLS</code>). Everything above is live from the Convex-fed DB.</div></div>`;
  }
  const cv=f.call_volume||0, pv=f.put_volume||0, tot=cv+pv||1;
  const cf=cv/tot*100;
  return `<div class="card">
    <div class="lbl">Put / Call volume · live</div>
    <div style="text-align:center;margin:10px 0 2px"><span class="num" style="font-size:22px;font-weight:700">${f.pc_ratio==null?'n/a':fmtC(f.pc_ratio,2)}</span>
      <span style="font-size:12px;color:var(--mut)"> P/C</span></div>
    <div class="pcbar"><div class="cf" style="width:${cf.toFixed(1)}%"></div><div class="pf" style="width:${(100-cf).toFixed(1)}%"></div></div>
    <div class="pcrow"><div class="c">CALLS <b>${abbr(cv,0)}</b></div><div class="p"><b>${abbr(pv,0)}</b> PUTS</div></div>
    <div class="brk"><div class="h"><div class="t">TRADED Δ-NOTIONAL</div></div>
      <div class="pcrow" style="margin-top:2px">
        <div class="c">calls <b>${abbr(f.call_notional)}</b></div>
        <div class="p">puts <b>${abbr(f.put_notional)}</b></div></div></div></div>`;
}

function skewCard(p){
  const s=p.skew;
  const cell=(kk,v,ss)=>{const t=vols(v); const cls=t==null?'na':(v>=0?'red':'grn');
    return `<div class="cell"><div class="kk">${kk}</div><div class="vv ${cls}">${t==null?'n/a':t}</div><div class="ss">${ss}</div></div>`;};
  return `<div class="card sk"><div class="lbl">Skew · 25Δ risk-reversal (put − call, vols)</div>
    <div class="grid">
      ${cell('0DTE RR25', s.rr25_0dte, 'put bid')}
      ${cell('30D RR25', s.rr25_30d, 'put bid')}
      ${cell('30D RR10', s.rr10_30d, 'tails')}
    </div>
    <div class="fine">ATM IV ${pct(s.atm_iv,1)} · positive = downside puts richer (fear)</div></div>`;
}

function render(p){
  $("cards").innerHTML = regimeCard(p)+emCard(p)+dpCard(p)+flowCard(p)+skewCard(p);
  $("footmeta").textContent = `${p.symbol} · ${p.meta.n_contracts} contracts · as of ${p.as_of.replace('T',' ')} · source ${p.meta.source}`;
}

function show(){
  const p = PAYLOADS[current];
  if(!p){ $("cards").innerHTML = `<div class="card">No snapshot for ${current}.</div>`; $("dot").className="dot stale"; $("upd").textContent="no data"; return; }
  render(p);
  $("err").style.display="none";
  $("dot").className="dot";
  $("upd").textContent = "as of "+String(p.as_of||"").replace("T"," ");
}

buildToggle();
show();
</script>
</body>
</html>
"""


def _collect(session, symbols: tuple[str, ...]) -> dict:
    """Build the per-symbol cockpit payloads from the Convex-fed DB (no vendor calls)."""
    from trading_intel.api.positioning import build_positioning

    payloads: dict = {}
    for sym in symbols:
        try:
            payloads[sym] = build_positioning(session, sym)
        except Exception as exc:  # noqa: BLE001 — one bad symbol shouldn't kill the report
            log.warning("cockpit.symbol_failed", symbol=sym, error=str(exc))
    return payloads


def build(
    *,
    symbols: tuple[str, ...] | None = None,
    out_path: str | None = None,
    settings: object = None,
    session: object = None,
) -> str:
    """Render the latest positioning snapshot into one HTML file.

    Symbols default to the configured index roots (``INDEX_ROOTS`` = SPX/SPY/QQQ)
    when not given, so the set is driven from ``.env`` (no code change to add or
    drop an index). Reads the Convex-fed DB via ``api.positioning.build_positioning``
    (no vendor calls) and bakes every index payload into one self-contained page.
    Opens its own session unless one is passed. Returns the absolute path.
    """
    from trading_intel.config import get_settings

    settings = settings or get_settings()
    roots = tuple(symbols) if symbols else tuple(getattr(settings, "index_roots", None) or _SYMBOLS)
    if session is not None:
        payloads = _collect(session, roots)
    else:
        from trading_intel.memory.db import make_session_factory

        with make_session_factory(settings)() as s:
            payloads = _collect(s, roots)

    # Escape any "</" so embedded JSON can never terminate the <script> block early.
    data = json.dumps(payloads).replace("</", "<\\/")
    html = _TEMPLATE_HTML.replace("__COCKPIT_DATA__", data)
    out = (Path(out_path) if out_path else _DEFAULT_OUT).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return str(out)


def run(
    *,
    symbols: tuple[str, ...] | None = None,
    push: bool = True,
    settings: object = None,
) -> str:
    """Build the cockpit and (optionally) push it to Telegram. Returns the path."""
    from trading_intel.config import get_settings

    settings = settings or get_settings()
    path = build(symbols=symbols, settings=settings)
    if push:
        from trading_intel.clients.telegram import TelegramClient

        sent = TelegramClient(settings).send_document(
            path, caption="Index dealer-positioning cockpit (SPX / SPY / QQQ)"
        )
        log.info("cockpit.pushed", path=path, telegram_sent=sent)
    return path


def main() -> None:
    """Manual/scheduled entrypoint: build the cockpit and push it to Telegram."""
    import argparse

    parser = argparse.ArgumentParser(description="Build the dealer-positioning cockpit report.")
    parser.add_argument("--no-push", action="store_true", help="build only; do not push to Telegram")
    parser.add_argument(
        "--symbols",
        default=",".join(_SYMBOLS),
        help="comma-separated index roots to bake in (default: config INDEX_ROOTS = SPX,SPY,QQQ)",
    )
    args = parser.parse_args()

    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    symbols = tuple(s.strip().upper() for s in args.symbols.split(",") if s.strip())
    path = run(symbols=symbols or _SYMBOLS, push=not args.no_push)
    print(f"cockpit written: {path}")


if __name__ == "__main__":
    main()
