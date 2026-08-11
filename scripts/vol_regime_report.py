"""SPX Vol-Regime & Skew Monitor — index vol-structure trend grid + detail → one
self-contained HTML, pushed to Telegram.

Canonical generator + CLI. Reproduces the CBOE skew/vol read from banked tables:
``get_index_skew`` (25Δ skew, CBOE SKEW, SDEX, VVIX, corr/dispersion, tail-hedge),
``get_iv_tenor`` (SPX/QQQ/SPY 15/25Δ wings, 1M/3M), ``get_vix`` (term + VRP).

Leads with a TREND-CHART grid (read the slope before a signal fires), then KPI
tiles, the skew-collapse table, cross-asset convexity, the VIX term curve, the
vol-of-vol/corr/tails table, and the CBOE-coverage map. Descriptor only (rule 4).

PHONE RULE: rendered ENTIRELY server-side (no <script>, no CDN).

Run:
    python scripts/vol_regime_report.py            # build + push to Telegram
    python scripts/vol_regime_report.py --no-push  # build only
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

_DEFAULT_OUT = Path("reports") / "vol_regime.html"
_WIN = 20  # trend window (trading days)
_RED = "#d1495b"
_AMBER = "#e08a1e"
_PURPLE = "#7c5cff"
_TEAL = "#2f9e6f"
_BLUE = "#2f6df0"
_MUT = "#9aa3b2"


# ── data ─────────────────────────────────────────────────────────────────────
def _collect(session) -> dict:
    from trading_intel.mcp.extra_tools import get_index_skew, get_iv_tenor, get_vix

    skew = get_index_skew(session, days=60).get("rows") or []
    vix = get_vix(session, days=45).get("rows") or []
    ivt = get_iv_tenor(session, symbols=["SPX", "QQQ", "SPY"], tenor_dte=30)
    return {"skew": skew, "vix": vix, "iv_rows": ivt.get("rows") or [], "iv_latest": ivt.get("latest") or []}


def _weekday(iso: str) -> bool:
    try:
        return date.fromisoformat(iso[:10]).weekday() < 5
    except (ValueError, TypeError):
        return True


def _series(rows, dkey, vkey, *, scale=1.0, weekday_only=True):
    """[(mm-dd, value)] for the last _WIN rows where value is not None."""
    out = []
    for r in rows:
        d = r.get(dkey)
        v = r.get(vkey)
        if d is None or v is None:
            continue
        if weekday_only and not _weekday(str(d)):
            continue
        out.append((str(d)[5:10], float(v) * scale))
    return out[-_WIN:]


def _latest(rows, key):
    for r in reversed(rows):
        if r.get(key) is not None:
            return r.get(key)
    return None


# ── svg ──────────────────────────────────────────────────────────────────────
def _chart(title, series, color, *, fmt="{:.1f}", unit="", pct=None, series2=None, lab1=None, lab2=None):
    if len(series) < 2:
        return f'<div class="ch"><div class="chh"><span class="cht">{title}</span></div>' \
               f'<div style="color:#9aa3b2;font-size:12px;padding:8px 0">insufficient data</div></div>'
    dates = [d for d, _ in series]
    vals = [v for _, v in series]
    v2 = [v for _, v in series2] if series2 else None
    w, h, pl, pr, pt, pb = 330, 118, 40, 12, 22, 26
    allv = vals + (v2 or [])
    lo, hi = min(allv), max(allv)
    rng = (hi - lo) or 1.0
    X = lambda i, n: pl + i * (w - pl - pr) / (n - 1)
    Y = lambda v: pt + (hi - v) / rng * (h - pt - pb)
    P = lambda vs: "M" + " L".join(f"{X(i,len(vs)):.1f},{Y(v):.1f}" for i, v in enumerate(vs))
    arrow = "&#9650;" if vals[-1] > vals[0] else ("&#9660;" if vals[-1] < vals[0] else "&#8212;")
    arrc = "#1f7a52" if vals[-1] > vals[0] else ("#b23048" if vals[-1] < vals[0] else _MUT)
    s = [f'<svg viewBox="0 0 {w} {h}" width="100%" xmlns="http://www.w3.org/2000/svg" '
         f'font-family="ui-sans-serif,system-ui,sans-serif">']
    for v in (hi, lo):
        s.append(f'<text x="{pl-5}" y="{Y(v)+3:.1f}" fill="{_MUT}" font-size="8.5" '
                 f'text-anchor="end">{fmt.format(v)}</text>')
        s.append(f'<line x1="{pl}" y1="{Y(v):.1f}" x2="{w-pr}" y2="{Y(v):.1f}" stroke="#eef1f6"/>')
    if v2:
        s.append(f'<path d="{P(v2)}" fill="none" stroke="{color}" stroke-width="1.6" '
                 f'stroke-dasharray="4 3" opacity="0.7"/>')
    s.append(f'<path d="{P(vals)}" fill="none" stroke="{color}" stroke-width="2.1" stroke-linejoin="round"/>')
    s.append(f'<circle cx="{X(len(vals)-1,len(vals)):.1f}" cy="{Y(vals[-1]):.1f}" r="3.6" '
             f'fill="{color}" stroke="#fff" stroke-width="1.4"/>')
    for i in (0, len(dates) // 2, len(dates) - 1):
        s.append(f'<text x="{X(i,len(dates)):.1f}" y="{h-7}" fill="{_MUT}" font-size="8" '
                 f'text-anchor="middle">{dates[i]}</text>')
    s.append("</svg>")
    chip = ""
    if pct is not None:
        pc = "p-lo" if pct <= 0.15 else ("p-hi" if pct >= 0.85 else "p-mid")
        chip = f'<span class="pct {pc}">{int(round(pct*100))}%ile</span>'
    leg = (f'<span class="leg2"><span class="ln" style="background:{color}"></span>{lab1} '
           f'&nbsp;<span class="ln dsh" style="border-top-color:{color}"></span>{lab2}</span>') if v2 else ""
    return (f'<div class="ch"><div class="chh"><span class="cht">{title}</span>{chip}</div>'
            f'<div class="chv" style="color:{color}">{fmt.format(vals[-1])}{unit} '
            f'<span class="arr" style="color:{arrc}">{arrow}</span></div>{"".join(s)}{leg}</div>')


def _spark(series, color):
    vals = [v for _, v in series]
    if len(vals) < 2:
        return ""
    w, h, pad = 150, 34, 3
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1.0
    pts = [(pad + i * (w - 2 * pad) / (len(vals) - 1), h - pad - (v - lo) / rng * (h - 2 * pad))
           for i, v in enumerate(vals)]
    d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    return (f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" xmlns="http://www.w3.org/2000/svg">'
            f'<path d="{d} L{pts[-1][0]:.1f},{h-pad} L{pts[0][0]:.1f},{h-pad} Z" fill="{color}" opacity="0.10"/>'
            f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.8" stroke-linejoin="round"/>'
            f'<circle cx="{pts[-1][0]:.1f}" cy="{pts[-1][1]:.1f}" r="2.7" fill="{color}"/></svg>')


def _curve_svg(curve):
    ys = [v for _, v in curve]
    if len(ys) < 2:
        return ""
    w, h, pad = 300, 120, 30
    lo, hi = min(ys) - 2, max(ys) + 2
    rng = hi - lo
    pts = [(pad + i * (w - 2 * pad) / (len(curve) - 1), h - pad - (v - lo) / rng * (h - 2 * pad))
           for i, (_, v) in enumerate(curve)]
    d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    o = [f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}" xmlns="http://www.w3.org/2000/svg" '
         f'font-family="ui-sans-serif,system-ui,sans-serif">',
         f'<path d="{d}" fill="none" stroke="#2f6df0" stroke-width="2.2" stroke-linejoin="round"/>']
    for (lab, v), (x, y) in zip(curve, pts):
        o.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="#2f6df0"/>')
        o.append(f'<text x="{x:.1f}" y="{y-9:.1f}" fill="#1f2733" font-size="12" font-weight="600" '
                 f'text-anchor="middle">{v:.1f}</text>')
        o.append(f'<text x="{x:.1f}" y="{h-8:.1f}" fill="#7a8699" font-size="11" '
                 f'text-anchor="middle">{lab}</text>')
    o.append("</svg>")
    return "".join(o)


def _num(x, fmt="{:.1f}", dash="&#8212;"):
    return fmt.format(x) if isinstance(x, (int, float)) else dash


# ── render ───────────────────────────────────────────────────────────────────
def _render_html(data: dict) -> str:
    skew, vixr, iv_rows, iv_latest = data["skew"], data["vix"], data["iv_rows"], data["iv_latest"]
    asof = str((skew[-1].get("date") if skew else "") or (vixr[-1].get("date") if vixr else ""))[:10] or "latest"

    # trend series
    rr_ix = _series(skew, "date", "spx_rr_25d_30d", scale=100.0)
    cboe = _series(skew, "date", "cboe_skew")
    sdex = _series(skew, "date", "sdex")
    vvix = _series(skew, "date", "vvix")
    tail = _series(skew, "date", "vix_tail_hedging_score")
    cor1 = _series(skew, "date", "cor1m")
    cor3 = _series(skew, "date", "cor3m")
    dspx = _series(skew, "date", "dspx")
    vvr = _series(skew, "date", "vvix_vix_ratio")
    vix = _series(vixr, "date", "vix")
    vrp = _series(vixr, "date", "vrp")
    rr_iv = _series([r for r in iv_rows if r.get("symbol") == "SPX"], "ts", "rr_25d", scale=100.0)

    # latest percentiles
    p_rr = _latest(skew, "spx_rr_pctile_252d")
    p_sdex = _latest(skew, "sdex_pctile_252d")
    p_cor = _latest(skew, "cor1m_pctile_252d")
    p_dspx = _latest(skew, "dspx_pctile_252d")

    charts = "".join([
        _chart("SPX 25&#916; skew &#183; iv_tenor RR", rr_iv, _RED, fmt="{:.1f}", unit=" pt"),
        _chart("SPX 25&#916; risk reversal &#183; index_skew", rr_ix, _RED, fmt="{:.1f}", pct=p_rr),
        _chart("CBOE SKEW index", cboe, _RED, fmt="{:.0f}"),
        _chart("SDEX", sdex, _RED, fmt="{:.1f}", pct=p_sdex),
        _chart("VIX", vix, _AMBER, fmt="{:.1f}"),
        _chart("Vol risk premium (VRP)", vrp, _AMBER, fmt="{:.1f}"),
        _chart("VVIX / VIX ratio", vvr, _PURPLE, fmt="{:.2f}"),
        _chart("Tail-hedge score", tail, _PURPLE, fmt="{:.2f}"),
        _chart("Implied corr &#183; 1M &amp; 3M", cor1, _TEAL, fmt="{:.1f}", pct=p_cor,
               series2=cor3, lab1="1M", lab2="3M"),
        _chart("DSPX (dispersion)", dspx, _TEAL, fmt="{:.1f}", pct=p_dspx),
    ])

    # KPI tiles
    def kpi(label, val, color, sub, spark):
        return (f'<div class="kpi"><div class="k">{label}</div><div class="v" style="color:{color}">{val}</div>'
                f'<div class="d">{sub}</div><div class="spark">{spark}</div></div>')
    kpis = "".join([
        kpi("SPX 25&#916; skew", f"{int(round(p_rr*100))}%ile" if p_rr is not None else "&#8212;",
            _RED, "252d pctile", _spark(rr_ix, _RED)),
        kpi("VIX", _num(vix[-1][1] if vix else None), _AMBER, "index", _spark(vix, _AMBER)),
        kpi("Vol risk premium", _num(vrp[-1][1] if vrp else None, "{:.2f}"), _TEAL,
            "IV &#8722; forecast RV", _spark(vrp, _TEAL)),
        kpi("Implied corr (1M)", _num(cor1[-1][1] if cor1 else None), _PURPLE,
            f"{int(round(p_cor*100))}%ile" if p_cor is not None else "", _spark(cor1, _PURPLE)),
    ])

    # cross-asset skew + convexity
    xa_rows = ""
    for r in sorted(iv_latest, key=lambda x: {"SPX": 0, "SPY": 1, "QQQ": 2}.get(x.get("symbol"), 9)):
        sym = r.get("symbol")
        rr = r.get("rr_25d")
        p25, p15 = r.get("iv_put_25d"), r.get("iv_put_15d")
        conv = (p15 / p25) if (p25 and p15) else None
        wpct = min(100, max(2, (rr or 0) / 0.06 * 100))
        xa_rows += (f'<tr><td style="font-weight:600">{sym}</td>'
                    f'<td><div class="barwrap"><div class="bar" style="width:{wpct:.0f}%"></div></div></td>'
                    f'<td class="num">{_num(rr, "{:+.4f}")}</td>'
                    f'<td class="num">{_num(conv, "{:.3f}")}&#215;</td></tr>')

    # VIX term curve (latest)
    vl = vixr[-1] if vixr else {}
    curve = [(lab, vl.get(k)) for lab, k in (("9d", "vix9d"), ("1M", "vix"), ("3M", "vix3m"), ("6M", "vix6m"))
             if isinstance(vl.get(k), (int, float))]
    curve_html = _curve_svg(curve)

    # vol-of-vol / corr / tails table
    def cell(x, fmt="{:.1f}"):
        return _num(x, fmt)
    vovol = (
        f'<tr><td>VVIX</td><td class="num">{cell(_latest(skew,"vvix"))}</td><td>vol-of-vol</td></tr>'
        f'<tr><td>VVIX / VIX ratio</td><td class="num">{cell(_latest(skew,"vvix_vix_ratio"),"{:.2f}")}</td>'
        f'<td>convexity demand vs VIX level</td></tr>'
        f'<tr><td>Implied corr 1M / 3M</td><td class="num">{cell(_latest(skew,"cor1m"))} / '
        f'{cell(_latest(skew,"cor3m"))}</td><td>single-name dispersion</td></tr>'
        f'<tr><td>DSPX (dispersion)</td><td class="num">{cell(_latest(skew,"dspx"))}</td>'
        f'<td>rolling off as corr rises</td></tr>'
        f'<tr><td>Tail-hedge score</td><td class="num">{cell(_latest(skew,"vix_tail_hedging_score"),"{:.2f}")}</td>'
        f'<td>tail re-hedging pressure</td></tr>'
    )

    rr_ix_last = f"{rr_ix[-1][1]/100:.3f}" if rr_ix else "&#8212;"
    rr_iv_last = f"{rr_iv[-1][1]:.1f} pt" if rr_iv else "&#8212;"

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>SPX Vol-Regime &amp; Skew Monitor &#8212; {asof}</title>
<style>
:root{{--bg:#f6f7f9;--card:#fff;--ink:#1f2733;--mut:#6b7686;--line:#e6e9ef;--blue:#2f6df0;--red:#d1495b;--amber:#e08a1e;--green:#2f9e6f;--purple:#7c5cff}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;line-height:1.55;font-size:15px}}
.wrap{{max-width:940px;margin:0 auto;padding:30px 20px 70px}}
.eyebrow{{color:var(--blue);font-weight:700;letter-spacing:.12em;text-transform:uppercase;font-size:11px}}
h1{{font-size:24px;margin:.3em 0 .1em}}.sub{{color:var(--mut);margin:.2em 0 0;font-size:14px}}
h2{{font-size:15px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);margin:2em 0 .6em;font-weight:700}}
.read{{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--red);border-radius:12px;padding:14px 17px;margin:16px 0;box-shadow:0 1px 2px rgba(20,30,50,.03)}}
.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:14px 0}}
.kpi{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 13px;box-shadow:0 1px 2px rgba(20,30,50,.03)}}
.kpi .k{{font-size:11px;color:var(--mut);text-transform:uppercase;letter-spacing:.05em}}.kpi .v{{font-size:22px;font-weight:750;margin:3px 0 1px}}.kpi .d{{font-size:11px;color:var(--mut)}}.kpi .spark{{margin-top:6px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}}
.ch{{background:var(--card);border:1px solid var(--line);border-radius:11px;padding:11px 12px 8px;box-shadow:0 1px 2px rgba(20,30,50,.03)}}
.chh{{display:flex;align-items:center;justify-content:space-between;gap:6px}}.cht{{font-size:11.5px;font-weight:700;color:#3a4356;text-transform:uppercase;letter-spacing:.03em}}
.chv{{font-size:19px;font-weight:800;margin:1px 0 3px}}.arr{{font-size:12px}}
.pct{{font-size:9.5px;font-weight:800;padding:2px 6px;border-radius:999px;white-space:nowrap}}
.p-lo{{background:#fdeaed;color:#b23048}}.p-mid{{background:#fff4e2;color:#a5641a}}.p-hi{{background:#e7f4ee;color:#1f7a52}}
.leg2{{font-size:9.5px;color:var(--mut);display:block;margin-top:2px}}.ln{{display:inline-block;width:12px;height:2px;vertical-align:2px;margin-right:3px}}.ln.dsh{{height:0;border-top:2px dashed}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:15px 17px;margin:11px 0;box-shadow:0 1px 2px rgba(20,30,50,.03)}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
table{{width:100%;border-collapse:collapse;font-size:13.5px}}th,td{{text-align:left;padding:8px;border-bottom:1px solid var(--line)}}
td.num{{text-align:right;font-variant-numeric:tabular-nums}}thead th{{font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--mut)}}tbody tr:last-child td{{border-bottom:none}}
.barwrap{{background:#eef1f6;border-radius:5px;height:12px;width:100%;overflow:hidden}}.bar{{background:linear-gradient(90deg,#d1495b,#e08a1e);height:100%;border-radius:5px}}
.pchip{{display:inline-block;min-width:40px;text-align:center;font-weight:700;font-size:11.5px;padding:2px 8px;border-radius:999px}}.p-lo2{{background:#fdeaed;color:#b23048}}
.tag{{display:inline-block;font-size:11px;font-weight:700;padding:2px 8px;border-radius:999px}}.t-have{{background:#e7f4ee;color:#1f7a52}}.t-gap{{background:#fdeaed;color:#b23048}}.t-part{{background:#fff4e2;color:#a5641a}}
.note{{color:var(--mut);font-size:12.5px}}ul{{margin:.4em 0;padding-left:1.15em}}li{{margin:.28em 0}}b{{color:#141b26}}
code{{background:#eef1f6;border-radius:5px;padding:1px 6px;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12.5px}}
.foot{{color:var(--mut);font-size:12px;border-top:1px solid var(--line);margin-top:26px;padding-top:13px}}
</style></head><body><div class="wrap">
<div class="eyebrow">Trading-Intel &#183; Index Vol Monitor</div>
<h1>SPX Vol-Regime &amp; Skew Monitor</h1>
<p class="sub">As of {asof} EOD &#183; trend grid + latest reads, from <code>get_index_skew</code> / <code>get_iv_tenor</code> / <code>get_vix</code>.</p>
<div class="read"><b>Read the slope.</b> Skew (both measures), SDEX and CBOE SKEW rolling toward their floors = hedges sold / calls chased; VIX + VRP compressing = vol asleep; VVIX/VIX and implied corr rising while DSPX rolls off = dispersion narrowing. The setup to watch: skew &amp; VRP compressing WHILE VVIX/VIX and corr climb.</div>
<div class="kpis">{kpis}</div>
<h2>Trend charts &#8212; read the slope before the signal fires</h2>
<div class="grid">{charts}</div>
<h2>1 &#183; SPX skew &#8212; latest &amp; percentile</h2>
<div class="card"><table>
<thead><tr><th>Measure</th><th class="num">Latest</th><th>252d pctile</th></tr></thead><tbody>
<tr><td>SPX 25&#916; RR &#183; index_skew</td><td class="num">{rr_ix_last}</td><td>{("<span class='pchip p-lo2'>"+str(int(round(p_rr*100)))+"th</span>") if p_rr is not None else "&#8212;"}</td></tr>
<tr><td>SPX 25&#916; RR &#183; iv_tenor</td><td class="num">{rr_iv_last}</td><td class="note">&#8212;</td></tr>
<tr><td>CBOE SKEW</td><td class="num">{_num(_latest(skew,"cboe_skew"),"{:.1f}")}</td><td class="note">&#8212;</td></tr>
<tr><td>SDEX</td><td class="num">{_num(_latest(skew,"sdex"))}</td><td>{("<span class='pchip p-lo2'>"+str(int(round(p_sdex*100)))+"th</span>") if p_sdex is not None else "&#8212;"}</td></tr>
</tbody></table>
<p class="note" style="margin-bottom:0"><b>Data-hygiene flag:</b> the two 25&#916; RR measures can disagree ~10&#215; (index_skew vs iv_tenor) — both charted above; reconcile which is canonical before this ships.</p></div>
<h2>2 &#183; Convexity &amp; cross-asset skew</h2>
<div class="grid2">
  <div class="card"><div class="note" style="margin-bottom:6px">25&#916; RR (1M) &amp; put convexity by index</div>
    <table><thead><tr><th>Index</th><th>25&#916; RR</th><th class="num">RR</th><th class="num">15&#916;/25&#916; put</th></tr></thead><tbody>{xa_rows}</tbody></table>
    <p class="note" style="margin-bottom:0">Convexity proxy = 15&#916;/25&#916; put-IV ratio (&gt;1 = deep puts bid). True 10&#916; needs the 10&#916; wing.</p></div>
  <div class="card"><div class="note" style="margin-bottom:6px">VIX term structure</div>{curve_html}
    <p class="note" style="margin-bottom:0">Contango steepness = the term read.</p></div>
</div>
<h2>3 &#183; Vol-of-vol, correlation &amp; tails</h2>
<div class="card"><table><thead><tr><th>Metric</th><th class="num">Latest</th><th>Context</th></tr></thead><tbody>{vovol}</tbody></table></div>
<h2>4 &#183; CBOE post &#8594; your data: coverage &amp; the 3 gaps</h2>
<div class="card"><table><thead><tr><th>CBOE metric</th><th>Source</th><th>Status</th></tr></thead><tbody>
<tr><td>25&#916; skew + 1yr pctile &#183; term &#183; VIX &#183; VRP &#183; corr &#183; VVIX &#183; SDEX</td><td>index_skew / iv_tenor / get_vix</td><td><span class="tag t-have">have</span></td></tr>
<tr><td>Put convexity &#8212; 10&#916; vs 25&#916; put</td><td>iv_tenor has 15/25&#916; only</td><td><span class="tag t-gap">gap</span></td></tr>
<tr><td>RTY 1M IV &amp; RTY&#8722;SPX spread</td><td>no IWM/RUT in iv_tenor</td><td><span class="tag t-gap">gap</span></td></tr>
<tr><td>Skew / term to 1Y</td><td>iv_tenor tops at 3M</td><td><span class="tag t-gap">gap</span></td></tr>
</tbody></table>
<p class="note" style="margin-bottom:0">Config adds: <code>IWM</code>&#8594;IV_TENOR_SYMBOLS (RTY-SPX). Schema adds: <code>10</code>&#8594;IV_TENOR_DELTAS + columns (10&#916; convexity); <code>365</code>&#8594;IV_TENOR_DTE + wider expiry pull (1Y). Percentiles are 252d (&#8776;1yr).</p></div>
<p class="foot">Sources &#8212; <code>get_index_skew</code>, <code>get_iv_tenor(SPX/QQQ/SPY,30d)</code>, <code>get_vix</code>. Server-side inline SVG. Trend window {_WIN} sessions (weekday-filtered); dashed = 3M where shown; arrow = window start&#8594;end. Descriptor only (rule 4).</p>
</div></body></html>"""


def build(*, out_path: str | None = None, settings: object = None, session: object = None) -> str:
    """Render the vol-regime monitor to one HTML file; return absolute path."""
    from trading_intel.config import get_settings

    settings = settings or get_settings()
    if session is not None:
        data = _collect(session)
    else:
        from trading_intel.memory.db import make_session_factory

        with make_session_factory(settings)() as s:
            data = _collect(s)
    html = _render_html(data)
    out = (Path(out_path) if out_path else _DEFAULT_OUT).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return str(out)


def main() -> None:
    import argparse

    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    parser = argparse.ArgumentParser(description="Build the SPX Vol-Regime & Skew Monitor.")
    parser.add_argument("--no-push", action="store_true", help="build only; do not push to Telegram")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    from trading_intel.config import get_settings

    settings = get_settings()
    path = build(out_path=args.out, settings=settings)
    print(f"vol_regime report: {path}")
    if not args.no_push:
        from trading_intel.clients.telegram import TelegramClient

        sent = TelegramClient(settings).send_document(path, caption="SPX Vol-Regime & Skew Monitor")
        print(f"telegram_sent={sent}")


if __name__ == "__main__":
    main()
