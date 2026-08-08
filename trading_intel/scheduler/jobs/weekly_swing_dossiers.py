"""Weekly job: rich swing-trade earnings dossier per monitored name reporting soon.

Universe = the WATCHLIST intersected with the forward earnings calendar
(``earnings_events``, from earn_cal) over the next ``days``. For each name, build
the standard 9-section dossier from the SAME reader layer the MCP tools use
(``mcp.tools`` / ``mcp.extra_tools``) + the ``kpi_snapshots`` /
``short_interest_snapshots`` / ``estimate_snapshots`` tables, render a
self-contained HTML with STATIC inline-SVG charts (score bars, expected-move
cone, IV-into-print sparkline, gamma-term bars, fresh-OI bars), rasterize a PNG
preview, and push BOTH to Telegram (photo preview + full HTML).

PHONE RULE (report-deploy-workflow): charts are STATIC inline SVG built here in
Python — NO client-side JS, NO CDN — so the HTML opens on phone; the PNG gives an
at-a-glance preview inline in Telegram. Descriptive research only (rule 4).

Manual run:
    python -m trading_intel.scheduler.jobs.weekly_swing_dossiers           # the week's reporters
    python -m trading_intel.scheduler.jobs.weekly_swing_dossiers NET CSCO  # specific names
"""

from __future__ import annotations

import html as _html
import os
import re
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.clients.telegram import TelegramClient
from trading_intel.config import Settings, get_settings
from trading_intel.mcp import extra_tools as et
from trading_intel.mcp import tools as t
from trading_intel.memory.models import (
    EarningsEvent,
    EstimateSnapshot,
    KpiSnapshot,
    ShortInterestSnapshot,
)
from trading_intel.timeutils import eastern_now

log = structlog.get_logger(__name__)
_OUT = Path("reports")


# ── scalars / format ───────────────────────────────────────────────────────
def _num(x):
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _f(x, nd=2):
    v = _num(x)
    return "—" if v is None else f"{v:,.{nd}f}"


def _lvl(v):
    return f"${_f(v, 0)}" if _num(v) is not None else "—"


def _esc(x):
    return _html.escape(str(x)) if x is not None else "—"


def _plain(s):
    return re.sub(r"<[^>]+>", "", s or "")


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


# ── STATIC inline SVG (colours via CSS classes; no JS, phone-safe) ─────────
def _cone(spot, lo, hi, marks):
    spot, lo, hi = _num(spot), _num(lo), _num(hi)
    if not (spot and lo and hi) or hi <= lo:
        return '<div class="na">expected move unavailable</div>'
    W, H, pad = 620, 70, 46
    span = hi - lo
    x = lambda v: round(pad + (v - lo) / span * (W - 2 * pad), 1)
    out = [f'<svg viewBox="0 0 {W} {H}" width="100%">',
           f'<line x1="{pad}" y1="36" x2="{W-pad}" y2="36" class="axis"/>']
    placed = []
    for v, lab, cls in marks:
        v = _num(v)
        if v is None or v < lo or v > hi:
            continue
        xv = x(v)
        up = not any(abs(xv - px) < 46 for px in placed)  # stagger crowded labels
        placed.append(xv)
        ly, ty = (14, 24) if up else (58, 68)
        out.append(f'<line x1="{xv}" y1="22" x2="{xv}" y2="50" class="{cls}"/>')
        out.append(f'<circle cx="{xv}" cy="36" r="3" class="{cls}"/>')
        out.append(f'<text x="{xv}" y="{ly}" text-anchor="middle" class="mk">{lab}</text>')
        out.append(f'<text x="{xv}" y="{ty}" text-anchor="middle" class="mv">${v:g}</text>')
    out.append("</svg>")
    return "".join(out)


def _spark(series, cls="pu"):
    vals = [v for v in (_num(s) for s in series) if v is not None]
    if len(vals) < 3:
        return '<div class="na">no IV history</div>'
    W, H, pad = 620, 54, 6
    mn, mx = min(vals), max(vals)
    rng = (mx - mn) or 1.0
    n = len(vals)
    pts = " ".join(
        f"{round(pad+i/(n-1)*(W-2*pad),1)},{round(H-pad-(v-mn)/rng*(H-2*pad),1)}"
        for i, v in enumerate(vals)
    )
    lastx = round(pad + (W - 2 * pad), 1)
    lasty = round(H - pad - (vals[-1] - mn) / rng * (H - 2 * pad), 1)
    return (f'<svg viewBox="0 0 {W} {H}" width="100%">'
            f'<polyline points="{pts}" class="{cls}"/>'
            f'<circle cx="{lastx}" cy="{lasty}" r="3" class="{cls}d"/></svg>')


def _hbars(items, unit=""):
    items = [(l, _num(v), c) for l, v, c in items if _num(v) is not None]
    if not items:
        return '<div class="na">no data</div>'
    mx = max(abs(v) for _, v, _ in items) or 1.0
    W, rowh, pad = 620, 22, 66
    H = rowh * len(items) + 6
    out = [f'<svg viewBox="0 0 {W} {H}" width="100%">']
    for i, (lab, v, cls) in enumerate(items):
        y = i * rowh + 4
        w = round(abs(v) / mx * (W - pad - 66), 1)
        out.append(f'<text x="0" y="{y+13}" class="bl">{_esc(lab)}</text>')
        out.append(f'<rect x="{pad}" y="{y+3}" width="{w}" height="12" rx="2" class="{cls}"/>')
        out.append(f'<text x="{pad+w+5}" y="{y+13}" class="bv">{v:g}{unit}</text>')
    out.append("</svg>")
    return "".join(out)


def _scorebars(scores):
    rows = [(l, v) for l, v in scores if v is not None]
    if not rows:
        return '<div class="na">scores unavailable</div>'
    W, rowh = 620, 26
    H = rowh * len(rows) + 4
    out = [f'<svg viewBox="0 0 {W} {H}" width="100%">']
    for i, (lab, v) in enumerate(rows):
        y = i * rowh + 4
        cls = "sg" if v >= 67 else "sb" if v >= 50 else "sa" if v >= 38 else "sr"
        w = round(v / 100 * (W - 150), 1)
        out.append(f'<text x="0" y="{y+15}" class="bl">{_esc(lab)}</text>')
        out.append(f'<rect x="118" y="{y+4}" width="{W-150}" height="13" rx="3" class="trk"/>')
        out.append(f'<rect x="118" y="{y+4}" width="{w}" height="13" rx="3" class="{cls}"/>')
        out.append(f'<text x="{W-24}" y="{y+15}" text-anchor="end" class="bv {cls}t">{int(v)}</text>')
    out.append("</svg>")
    return "".join(out)


# ── scores ─────────────────────────────────────────────────────────────────
def _tech_score(ind):
    close, s20, s50 = _num(ind.get("close")), _num(ind.get("sma20")), _num(ind.get("sma50"))
    rsi, mh = _num(ind.get("rsi14")), _num(ind.get("macd_hist"))
    if close is None:
        return None
    s = 50.0
    if s20:
        s += 12 if close > s20 else -12
    if s50:
        s += 12 if close > s50 else -12
    if rsi is not None:
        s += (rsi - 50) * 0.4
    if mh is not None:
        s += 8 if mh > 0 else -8
    return int(_clamp(s, 0, 100))


def _mom_score(ind):
    close, s50, rsi = _num(ind.get("close")), _num(ind.get("sma50")), _num(ind.get("rsi14"))
    if close is None or s50 is None:
        return None
    s = 50.0 + _clamp((close / s50 - 1) * 300, -28, 28)
    if rsi is not None:
        s += (rsi - 50) * 0.3
    return int(_clamp(s, 0, 100))


def _optq_score(vr, skew, oi):
    s, got = 50.0, False
    rich = _num((vr or {}).get("richness_score"))
    if rich is not None:
        s += (0.5 - rich) * 40
        got = True
    bias = ((skew or {}).get("summary") or {}).get("bias")
    if bias:
        s += 12 if "call" in str(bias).lower() else -6
        got = True
    if oi and oi.get("net_call_oi_change") is not None:
        s += 10 if (_num(oi.get("net_call_oi_change")) or 0) > (_num(oi.get("net_put_oi_change")) or 0) else -10
        got = True
    return int(_clamp(s, 0, 100)) if got else None


def _fund_score(cur, est):
    if cur is None:
        return None
    s, got = 50.0, False
    rg = _num(getattr(cur, "revenue_growth_yoy_pct", None))
    if rg is not None:
        s += _clamp(rg * 0.8, -30, 40)
        got = True
    gm = _num(getattr(cur, "gross_margin_pct", None))
    if gm is not None:
        s += _clamp((gm - 60) * 0.4, -12, 12)
        got = True
    return int(_clamp(s, 0, 100)) if got else None


# ── DB reads ────────────────────────────────────────────────────────────────
def _latest_kpis(session, sym):
    return list(session.execute(
        select(KpiSnapshot).where(KpiSnapshot.symbol == sym)
        .order_by(KpiSnapshot.period_label.desc()).limit(2)).scalars())


def _latest_short(session, sym):
    out = {}
    for src in ("finra_si", "regsho_daily"):
        row = session.execute(
            select(ShortInterestSnapshot)
            .where(ShortInterestSnapshot.symbol == sym, ShortInterestSnapshot.source == src)
            .order_by(ShortInterestSnapshot.ts.desc()).limit(1)).scalar_one_or_none()
        if row is not None:
            out[src] = row
    return out


def _latest_estimate(session, sym):
    return session.execute(
        select(EstimateSnapshot).where(EstimateSnapshot.symbol == sym)
        .order_by(EstimateSnapshot.ts.desc()).limit(1)).scalar_one_or_none()


# ── assemble one name ────────────────────────────────────────────────────────
def build_one(session, sym, *, settings, ev=None):
    sym = sym.upper()
    d = {}
    d["straddle"] = et.get_straddle(session, sym)
    d["walls"] = et.get_walls(session, sym)
    d["gexterm"] = et.get_gex_term(session, sym)
    d["gamma"] = t.get_gamma_history(session, sym, days=5)
    d["skew"] = t.get_skew_history(session, sym, horizon_dte=30)
    d["tech"] = t.get_technicals(session, sym, days=210)
    d["oi"] = et.get_oi_changes(session, sym, top=8)
    try:
        vrall = et.get_vol_richness(session, [sym], settings=settings)
        d["vr"] = (vrall.get("rows") or [{}])[0] if vrall else {}
    except Exception:
        d["vr"] = {}
    try:
        flowall = t.get_watchlist_flow(session, [sym], settings=settings)
        d["flow"] = (flowall.get("rows") or [{}])[0] if flowall else {}
    except Exception:
        d["flow"] = {}
    try:
        d["note"] = et.get_research_note(session, sym)
    except Exception:
        d["note"] = {}
    d["kpis"] = _latest_kpis(session, sym)
    d["shorts"] = _latest_short(session, sym)
    d["est"] = _latest_estimate(session, sym)

    if not (d["straddle"] and d["straddle"].get("found")) and not (d["tech"] and d["tech"].get("found")):
        return None, None
    html_doc = _render(sym, ev, d)
    _OUT.mkdir(parents=True, exist_ok=True)
    dest = _OUT / f"{sym}_swing_{date.today().isoformat()}.html"
    dest.write_text(html_doc, encoding="utf-8")
    return dest, _caption(sym, ev, d)


def _caption(sym, ev, d):
    st, vr, sk = d["straddle"] or {}, d["vr"] or {}, (d["skew"] or {}).get("summary") or {}
    when = f" · {ev['date']} {ev.get('time') or ''}".rstrip() if ev and ev.get("date") else ""
    em = _f(st.get("straddle_pct"))
    parts = [f"<b>{sym}</b> swing dossier{when}", f"Expected move ±{em}%"]
    if vr.get("label"):
        parts.append(f"IV: {vr['label'].split('(')[0].strip()}")
    if sk.get("label"):
        parts.append(f"Skew: {sk['label']}")
    cw, pw = _num((d['walls'] or {}).get('call_wall')), _num((d['walls'] or {}).get('put_wall'))
    if cw or pw:
        parts.append(f"Walls {_lvl(cw)}/{_lvl(pw)}")
    return "\n".join(parts)


def _render(sym, ev, d):
    st = d["straddle"] or {}
    walls = d["walls"] or {}
    gexterm = d["gexterm"] or {}
    grows = (d["gamma"] or {}).get("rows") or []
    glast = grows[-1] if grows else {}
    skew = d["skew"] or {}
    sksum = skew.get("summary") or {}
    ind = (d["tech"] or {}).get("indicators") or {}
    oi = d["oi"] or {}
    vr = d["vr"] or {}
    flow = d["flow"] or {}
    kpis, shorts, est = d["kpis"], d["shorts"], d["est"]

    spot = _num(st.get("spot")) or _num(ind.get("close"))
    empct = _num(st.get("straddle_pct"))
    lo, hi = _num(st.get("lower")), _num(st.get("upper"))
    cw, pw = _num(walls.get("call_wall")), _num(walls.get("put_wall"))
    flip = _num(glast.get("gex_flip"))
    regime = glast.get("regime") or (d["gamma"] or {}).get("summary", {}).get("current_regime")
    dex = _num(glast.get("dex_total"))
    rr = _num(sksum.get("current_rr_25d"))
    rrpc = _num(sksum.get("current_pctile_252d"))
    atm = _num(st.get("atm_iv"))
    nc, npu = _num(oi.get("net_call_oi_change")), _num(oi.get("net_put_oi_change"))

    # charts
    cone = _cone(spot, lo, hi, [
        (lo, "EM low", "dn"), (pw, "put wall", "dn"), (flip, "flip", "fl"),
        (spot, "spot", "sp"), (cw, "call wall", "cw"), (hi, "EM high", "up")])
    iv_series = [r.get("atm_iv") for r in (skew.get("rows") or [])][-16:]
    ivspark = _spark(iv_series)
    gbars = _hbars([(str(r.get("expiration", ""))[5:], _num(r.get("gex")), "gx") for r in (gexterm.get("term") or [])[:8]])
    oibars = _hbars([("Δ calls", nc, "up"), ("Δ puts", npu, "dn")]) if (nc is not None or npu is not None) else '<div class="na">no fresh OI</div>'

    cur = kpis[0] if kpis else None
    prv = kpis[1] if len(kpis) > 1 else None
    scores = [
        ("Technical", _tech_score(ind)),
        ("Momentum", _mom_score(ind)),
        ("Option-quality", _optq_score(vr, skew, oi)),
        ("Fundamental", _fund_score(cur, est)),
    ]
    scorebars = _scorebars(scores)

    def _kpi(lbl, attr, bar=""):
        c = _num(getattr(cur, attr, None)) if cur else None
        p = _num(getattr(prv, attr, None)) if prv else None
        cs = "—" if c is None else f"{c:g}%"
        delta = ""
        if c is not None and p is not None:
            dv = c - p
            delta = f' <span class="{"up" if dv>=0 else "dn"}">({dv:+g})</span>'
        return f'<tr><td>{lbl}</td><td>{cs}{delta}</td><td class="mut">{bar}</td></tr>'

    kpi_html = '<div class="na">No transcript KPIs banked yet (run kpi_snapshots).</div>'
    guide = one = None
    if cur:
        kpi_html = ("<table>"
                    + _kpi("DBNRR (net retention)", "dbnrr_pct", "the swing factor — a QoQ slide is what breaks the stock")
                    + _kpi("Revenue growth YoY", "revenue_growth_yoy_pct", "re-accel vs decel")
                    + _kpi("Gross margin", "gross_margin_pct", "compression = bear")
                    + _kpi("cRPO growth YoY", "crpo_growth_yoy_pct", "leading indicator")
                    + _kpi("Operating margin", "operating_margin_pct", "Rule-of-40")
                    + "</table>")
        guide = getattr(cur, "guidance_direction", None)
        one = getattr(cur, "one_line_kpi_read", None)

    # research note (Hidden Angle)
    note_md = (d["note"] or {}).get("note_md") or ""
    angle = ""
    if "Uploaded research excerpt" in note_md:
        angle = note_md.split("Uploaded research excerpt", 1)[1].strip(" \n#")[:1000]
    elif note_md:
        angle = note_md.replace("### ", "").strip()[:1000]

    # short interest
    si, rs = shorts.get("finra_si"), shorts.get("regsho_daily")
    si_line = "—"
    if si is not None and _num(getattr(si, "days_to_cover", None)) is not None:
        si_line = f"Days-to-cover {_f(si.days_to_cover)} · SI {_f(si.short_interest,0)}"
    elif rs is not None and _num(getattr(rs, "short_volume_ratio_avg", None)) is not None:
        si_line = f"Reg SHO 10d short-vol ratio {_f((rs.short_volume_ratio_avg or 0)*100)}%"

    # estimates
    est_line = "—"
    if est is not None:
        pp = []
        if _num(getattr(est, "eps_avg", None)) is not None:
            pp.append(f"EPS est {_f(est.eps_avg)}")
        if _num(getattr(est, "revenue_avg", None)) is not None:
            pp.append(f"rev est {_f(est.revenue_avg,0)}")
        est_line = " · ".join(pp) or "—"

    # one-line read
    reads = []
    if vr.get("label"):
        reads.append("rich vol" if "rich" in vr["label"] else "cheap vol" if "cheap" in vr["label"] else "neutral vol")
    if sksum.get("bias"):
        reads.append("upside-chasing skew" if "call" in str(sksum["bias"]).lower() else "protective skew")
    if nc is not None and npu is not None:
        reads.append("fresh calls building" if nc > npu else "fresh puts building")
    oneread = " · ".join(reads) if reads else "positioning read below"

    crush = ""
    if atm is not None:
        crush = (f'<div class="warn">Earnings-week ATM IV ≈ {atm*100:.0f}%. Post-print crush is steep — long '
                 f'single-legs fight direction <i>and</i> crush; favour spreads or a post-gap entry.</div>')
    rpt = f" · reports {_esc(ev.get('date'))} {_esc(ev.get('time') or '')}" if ev and ev.get("date") else ""

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{sym} — Swing Earnings Dossier</title>
<style>
:root{{--bg:#0f1216;--panel:#171b21;--p2:#1d222a;--ink:#e7ebf0;--mut:#9aa4b2;--line:#2a313b;--ac:#4f9cf0;--ac2:#f0a02a;--gn:#39b878;--rd:#e2564a;--pu:#a98bf0}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,Segoe UI,Roboto,Arial,sans-serif}}
.wrap{{max-width:780px;margin:0 auto;padding:20px 16px 60px}}h1{{font-size:22px;margin:0}}.tk{{color:var(--ac)}}
.sub{{color:var(--mut);font-size:13px}}.lead{{font-size:14px;margin:10px 0}}
h2{{font-size:13px;text-transform:uppercase;letter-spacing:.05em;margin:22px 0 8px;color:var(--ac);border-bottom:1px solid var(--line);padding-bottom:5px}}
h2 span{{color:var(--mut)}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:11px;padding:13px 15px;margin:9px 0}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:7px}}
.kv{{display:flex;justify-content:space-between;gap:10px;padding:4px 0;border-bottom:1px dashed var(--line);font-size:13.5px}}
.kv .k{{color:var(--mut)}}.kv b{{color:var(--ink)}}
table{{width:100%;border-collapse:collapse;font-size:13px}}td{{padding:5px 7px;border-bottom:1px solid var(--line);vertical-align:top}}td:first-child{{color:var(--mut)}}.mut{{color:var(--mut);font-size:12px}}
.na{{color:var(--mut);font-size:13px}}.up{{color:var(--gn)}}.dn{{color:var(--rd)}}.neu{{color:var(--ac2)}}
.warn{{background:rgba(226,86,74,.09);border-left:3px solid var(--rd);border-radius:0 8px 8px 0;padding:9px 12px;margin:8px 0;font-size:13px}}
.angle{{background:var(--p2);border-left:3px solid var(--gn);border-radius:0 8px 8px 0;padding:9px 12px;margin:8px 0;font-size:13px;white-space:pre-wrap}}
.cap{{font-size:11px;color:var(--mut);margin-top:4px}}
svg text{{fill:var(--mut);font-size:9px}}svg text.mk{{fill:var(--ink);font-weight:700}}svg text.mv{{fill:var(--mut)}}
svg text.bl{{fill:var(--mut);font-size:10.5px}}svg text.bv{{fill:#cdd5df;font-size:10.5px}}
.axis{{stroke:#39425090;stroke-width:6;stroke-linecap:round}}
line.sp,circle.sp{{stroke:#e7ebf0;fill:#e7ebf0;stroke-width:2}}line.cw,circle.cw{{stroke:#f0a02a;fill:#f0a02a}}
line.up,circle.up{{stroke:#39b878;fill:#39b878}}line.dn,circle.dn{{stroke:#e2564a;fill:#e2564a}}line.fl,circle.fl{{stroke:#a98bf0;fill:#a98bf0}}
rect.gx{{fill:#4f9cf0}}rect.up{{fill:#39b878}}rect.dn{{fill:#e2564a}}rect.trk{{fill:#232a33}}
rect.sg{{fill:#39b878}}rect.sb{{fill:#4f9cf0}}rect.sa{{fill:#f0a02a}}rect.sr{{fill:#e2564a}}
text.sgt{{fill:#39b878}}text.sbt{{fill:#8cc0f5}}text.sat{{fill:#f0a02a}}text.srt{{fill:#e2564a}}
polyline.pu{{fill:none;stroke:#a98bf0;stroke-width:2}}circle.pud{{fill:#a98bf0}}
.foot{{color:var(--mut);font-size:11px;margin-top:22px;border-top:1px solid var(--line);padding-top:10px}}
</style></head><body><div class="wrap">
<h1><span class="tk">{sym}</span> — Swing Earnings Dossier</h1>
<div class="sub">auto-generated {date.today().isoformat()}{rpt} · CVForge / trading-intel</div>
<div class="lead"><b>Read:</b> {_esc(oneread)}.</div>

<h2>1 · Expected move <span>· what's priced</span></h2>
<div class="card">
<div class="grid">
<div class="kv"><span class="k">Spot</span><b>${_f(spot)}</b></div>
<div class="kv"><span class="k">Implied move</span><b>±{_f(empct)}%</b></div>
<div class="kv"><span class="k">Straddle band</span><b>${_f(lo,0)}–${_f(hi,0)}</b></div>
<div class="kv"><span class="k">Consensus</span><b>{_esc(est_line)}</b></div>
</div>
{crush}
<div style="margin-top:8px">{cone}</div>
<div class="cap">IV into the print (30-day ATM)</div>{ivspark}</div>

<h2>2 · Dealer-gamma map <span>· the path</span></h2>
<div class="card">
<div class="grid">
<div class="kv"><span class="k">Net GEX</span><b>{_f(gexterm.get('gex_total'),0)}</b></div>
<div class="kv"><span class="k">Regime</span><b>{_esc(regime)}</b></div>
<div class="kv"><span class="k">Flip</span><b>{_lvl(flip)}</b></div>
<div class="kv"><span class="k">Call / Put wall</span><b>{_lvl(cw)} / {_lvl(pw)}</b></div>
<div class="kv"><span class="k">Dealer delta (DEX)</span><b>{_f(dex,0)}</b></div>
</div>
<div class="cap">GEX by expiry — thin front-week = gap over-realizes, then damps (EM-break)</div>{gbars}</div>

<h2>3 · Options positioning <span>· who's leaning</span></h2>
<div class="card">
<div class="kv"><span class="k">25Δ skew (RR)</span><b>{_f(rr,3)} · {_esc(sksum.get('label'))}{'' if rrpc is None else f' · p{rrpc*100:.0f}'}</b></div>
<div class="kv"><span class="k">IV vs RV (VRP)</span><b>{_esc(vr.get('label'))}</b></div>
<div class="kv"><span class="k">Flow tilt · PCR</span><b>{_esc(flow.get('tilt'))} · {_f(flow.get('put_call_ratio'))}</b></div>
<div class="cap" style="margin-top:6px">Fresh OI (day-over-day Δ by side)</div>{oibars}</div>

<h2>4 · Earnings KPI scorecard <span>· grade the print</span></h2>
<div class="card">{kpi_html}
{f'<div class="kv" style="margin-top:6px"><span class="k">Guidance</span><b>{_esc(guide)}</b></div>' if cur else ''}
{f'<div class="na" style="margin-top:6px">{_esc(one)}</div>' if one else ''}</div>

<h2>5 · Hidden-Angle / transcript read <span>· the thesis</span></h2>
<div class="card">{f'<div class="angle">{_esc(angle)}</div>' if angle else '<div class="na">No research note on file.</div>'}
<div class="mut" style="margin-top:8px">Watch on the call: net-retention tone (bottoming vs guiding lower), margin bridge (temporary vs structural), guidance confidence.</div></div>

<h2>6 · Institutional &amp; short interest</h2>
<div class="card">
<div class="kv"><span class="k">Short interest</span><b>{_esc(si_line)}</b></div>
<div class="mut" style="margin-top:4px">13F / Form-4 via edgartools = roadmap (FMP premium-gates them).</div></div>

<h2>7 · Factor scores</h2>
<div class="card">{scorebars}
<div class="mut" style="margin-top:6px">Heuristic composites (0–100) from the positioning + KPI data — not the deployed factor model. Higher = better.</div></div>

<h2>8 · Scenario playbook <span>· scenarios × levels</span></h2>
<div class="card">
<div class="kv"><span class="k up">Bull (beat + raise)</span><b>toward {_lvl(hi)} / call wall {_lvl(cw)}</b></div>
<div class="kv"><span class="k neu">Base (in-line)</span><b>pin near flip {_lvl(flip)}; rich IV crushes</b></div>
<div class="kv"><span class="k dn">Bear (miss)</span><b>toward {_lvl(lo)} / put wall {_lvl(pw)}</b></div>
<div class="na" style="margin-top:6px">Thin front-week gamma → the gap over-realizes then the back-dated gamma damps; the higher-conviction swing is often the post-earnings EM-break → burn-off re-entry.</div></div>

<div class="foot">Sources: CVForge/trading-intel — straddle · walls · gex_term · gamma_history · skew · technicals · oi_changes · vol_richness · watchlist_flow · research_note · kpi_snapshots · short_interest_snapshots · estimate_snapshots. Blank = source not yet populated. Not investment advice; flow/skew/OI/gamma are descriptive of positioning (rule 4).</div>
</div></body></html>"""


# ── PNG preview (best-effort; needs a headless browser) ─────────────────────
def _rasterize(html_path: Path) -> Path | None:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None
    png = html_path.with_suffix(".png")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 780, "height": 1400})
            page.goto("file://" + os.path.abspath(str(html_path)))
            page.wait_for_timeout(400)
            page.screenshot(path=str(png), full_page=True)
            browser.close()
        return png
    except Exception as exc:  # noqa: BLE001
        log.warning("swing_dossier.rasterize_failed", err=str(exc))
        return None


# ── universe + orchestration ────────────────────────────────────────────────
def _week_reporters(session, settings, *, days):
    today = eastern_now().date()
    horizon = today + timedelta(days=days)
    wl = {s.upper() for s in settings.watchlist_symbols}
    rows = session.execute(
        select(EarningsEvent.symbol, EarningsEvent.date, EarningsEvent.time)
        .where(EarningsEvent.date >= today, EarningsEvent.date <= horizon)
        .order_by(EarningsEvent.date)).all()
    seen, out = set(), []
    for sym, dt, tm in rows:
        s = (sym or "").upper()
        if s in wl and s not in seen:
            seen.add(s)
            out.append({"symbol": s, "date": dt.isoformat() if dt else None, "time": tm})
    return out


def run(session, *, settings=None, symbols=None, days=8, telegram=None):
    settings = settings or get_settings()
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="weekly_swing_dossiers")
    tg = telegram if telegram is not None else TelegramClient(settings)

    if symbols:
        targets = [{"symbol": s.upper(), "date": None, "time": None} for s in symbols]
    else:
        targets = _week_reporters(session, settings, days=days)

    written, lines = [], []
    for ev in targets:
        sym = ev["symbol"]
        try:
            dest, caption = build_one(session, sym, settings=settings, ev=ev)
        except Exception as exc:  # noqa: BLE001 — one bad name never kills the batch
            bound.warning("swing_dossier.skip", symbol=sym, err=str(exc))
            continue
        if dest is None:
            continue
        written.append(str(dest))
        png = _rasterize(dest)
        sent_img = tg.send_photo(png, caption=caption) if png is not None else False
        tg.send_document(dest, caption=("" if sent_img else _plain(caption)))
        when = f" ({ev['date']})" if ev.get("date") else ""
        lines.append(f"• <b>{sym}</b>{when}")

    if lines and tg.enabled:
        tg.send_message(f"<b>Swing earnings dossiers</b> — {len(written)} name(s) this week:\n" + "\n".join(lines))
    bound.info("weekly_swing_dossiers.done", n=len(written), symbols=[e["symbol"] for e in targets])
    return {"written": written}


def main():
    from trading_intel.memory.db import make_session_factory

    structlog.configure(processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ])
    settings = get_settings()
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    session_factory = make_session_factory(settings)
    with session_factory() as session:
        result = run(session, settings=settings, symbols=args or None)
    print(f"Done: {result}")


if __name__ == "__main__":
    main()
