"""Weekly job: swing-trade earnings dossier for each monitored name reporting soon.

Universe = the WATCHLIST intersected with the forward earnings calendar
(``earnings_events``, banked from earn_cal) over the next ``days`` — the names
Mithil monitors that report this coming week. For each, assemble the standard
9-section swing dossier from the SAME reader layer the MCP tools use
(``mcp.tools`` / ``mcp.extra_tools``) plus the ``kpi_snapshots`` /
``short_interest_snapshots`` / ``estimate_snapshots`` tables, render a
self-contained HTML, and push it to Telegram.

PHONE RULE (report-deploy-workflow): charts are STATIC inline SVG built here in
Python — NO client-side JS, NO CDN, small file — so the report opens in Telegram
ON PHONE, not just desktop. Every dossier is sent via ``TelegramClient`` with a
one-line caption; a summary index message leads.

Descriptive research only (FlashAlpha rule 4). Vendor access via the clients
(rule 1); the local research-note model is Ollama (rule 7).

Manual run:
    python -m trading_intel.scheduler.jobs.weekly_swing_dossiers           # the week's reporters
    python -m trading_intel.scheduler.jobs.weekly_swing_dossiers NET CSCO  # specific names
"""

from __future__ import annotations

import html as _html
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


# ── small helpers ─────────────────────────────────────────────────────────
def _num(x):
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _pct(x, nd: int = 1) -> str:
    v = _num(x)
    return "—" if v is None else f"{v:.{nd}f}%"


def _f(x, nd: int = 2) -> str:
    v = _num(x)
    return "—" if v is None else f"{v:,.{nd}f}"


def _esc(x) -> str:
    return _html.escape(str(x)) if x is not None else "—"


# ── STATIC inline SVG (no JS, colours via CSS classes — phone-safe) ────────
def _cone(spot, lo, hi, marks: list[tuple]) -> str:
    """Horizontal price band with markers. ``marks`` = [(value, label, css_class)]."""
    spot, lo, hi = _num(spot), _num(lo), _num(hi)
    if not (spot and lo and hi) or hi <= lo:
        return '<div class="na">expected move unavailable</div>'
    W, H, pad = 560, 66, 44
    span = hi - lo

    def x(v):
        return round(pad + (v - lo) / span * (W - 2 * pad), 1)

    out = [f'<svg viewBox="0 0 {W} {H}" width="100%">']
    out.append(f'<line x1="{pad}" y1="34" x2="{W-pad}" y2="34" class="axis"/>')
    for v, lab, cls in marks:
        v = _num(v)
        if v is None or v < lo or v > hi:
            continue
        xv = x(v)
        out.append(f'<line x1="{xv}" y1="20" x2="{xv}" y2="48" class="{cls}"/>')
        out.append(f'<circle cx="{xv}" cy="34" r="3" class="{cls}"/>')
        out.append(f'<text x="{xv}" y="15" text-anchor="middle" class="mk">{lab}</text>')
        out.append(f'<text x="{xv}" y="60" text-anchor="middle" class="mv">${v:g}</text>')
    out.append("</svg>")
    return "".join(out)


def _hbars(items: list[tuple], *, maxabs: float | None = None) -> str:
    """Horizontal bars: ``items`` = [(label, value, css_class)]. Static SVG."""
    items = [(l, _num(v), c) for l, v, c in items if _num(v) is not None]
    if not items:
        return '<div class="na">no data</div>'
    mx = maxabs or max(abs(v) for _, v, _ in items) or 1.0
    W, rowh, pad = 560, 22, 92
    H = rowh * len(items) + 8
    out = [f'<svg viewBox="0 0 {W} {H}" width="100%">']
    for i, (lab, v, cls) in enumerate(items):
        y = i * rowh + 4
        w = round(abs(v) / mx * (W - pad - 60), 1)
        out.append(f'<text x="0" y="{y+13}" class="bl">{_esc(lab)}</text>')
        out.append(f'<rect x="{pad}" y="{y+3}" width="{w}" height="12" rx="2" class="{cls}"/>')
        out.append(f'<text x="{pad+w+5}" y="{y+13}" class="bv">{v:g}</text>')
    out.append("</svg>")
    return "".join(out)


# ── data assembly per name ─────────────────────────────────────────────────
def _latest_kpis(session: Session, sym: str) -> list[KpiSnapshot]:
    return list(
        session.execute(
            select(KpiSnapshot)
            .where(KpiSnapshot.symbol == sym)
            .order_by(KpiSnapshot.period_label.desc())
            .limit(2)
        ).scalars()
    )


def _latest_short(session: Session, sym: str) -> dict:
    out: dict = {}
    for src in ("finra_si", "regsho_daily"):
        row = session.execute(
            select(ShortInterestSnapshot)
            .where(ShortInterestSnapshot.symbol == sym, ShortInterestSnapshot.source == src)
            .order_by(ShortInterestSnapshot.ts.desc())
            .limit(1)
        ).scalar_one_or_none()
        if row is not None:
            out[src] = row
    return out


def _latest_estimate(session: Session, sym: str) -> EstimateSnapshot | None:
    return session.execute(
        select(EstimateSnapshot)
        .where(EstimateSnapshot.symbol == sym)
        .order_by(EstimateSnapshot.ts.desc())
        .limit(1)
    ).scalar_one_or_none()


def _tech_score(ind: dict) -> int | None:
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
    return int(max(0, min(100, s)))


def _optq_score(vr: dict, skew: dict, oi: dict) -> int | None:
    """Higher = cleaner bullish positioning into cheaper vol."""
    s = 50.0
    got = False
    rich = _num(vr.get("richness_score")) if vr else None
    if rich is not None:  # 0 cheap … 1 rich → cheap is better for a long
        s += (0.5 - rich) * 40
        got = True
    bias = (skew or {}).get("summary", {}).get("bias") if skew else None
    if bias:
        s += 12 if "call" in str(bias).lower() else -6
        got = True
    if oi and oi.get("net_call_oi_change") is not None:
        nc = _num(oi.get("net_call_oi_change")) or 0
        npu = _num(oi.get("net_put_oi_change")) or 0
        s += 10 if nc > npu else -10
        got = True
    return int(max(0, min(100, s))) if got else None


def build_one(session: Session, sym: str, *, settings: Settings, ev: dict | None = None) -> Path | None:
    sym = sym.upper()
    straddle = et.get_straddle(session, sym)
    walls = et.get_walls(session, sym)
    gexterm = et.get_gex_term(session, sym)
    gamma = t.get_gamma_history(session, sym, days=5)
    skew = t.get_skew_history(session, sym, horizon_dte=30)
    tech = t.get_technicals(session, sym, days=210)
    oi = et.get_oi_changes(session, sym, top=8)
    try:
        vrall = et.get_vol_richness(session, [sym], settings=settings)
        vr = (vrall.get("rows") or [{}])[0] if vrall else {}
    except Exception:
        vr = {}
    try:
        flowall = t.get_watchlist_flow(session, [sym], settings=settings)
        flow = (flowall.get("rows") or [{}])[0] if flowall else {}
    except Exception:
        flow = {}
    try:
        note = et.get_research_note(session, sym)  # local Ollama — degrade if down
    except Exception:
        note = {}
    kpis = _latest_kpis(session, sym)
    shorts = _latest_short(session, sym)
    est = _latest_estimate(session, sym)

    if not (straddle and straddle.get("found")) and not (tech and tech.get("found")):
        return None  # no data at all for this name

    html_doc = _render(sym, ev, straddle, walls, gexterm, gamma, skew, tech, oi, vr, flow, note, kpis, shorts, est)
    _OUT.mkdir(parents=True, exist_ok=True)
    dest = _OUT / f"{sym}_swing_{date.today().isoformat()}.html"
    dest.write_text(html_doc, encoding="utf-8")
    return dest


def _render(sym, ev, straddle, walls, gexterm, gamma, skew, tech, oi, vr, flow, note, kpis, shorts, est) -> str:
    ind = (tech or {}).get("indicators") or {}
    spot = _num((straddle or {}).get("spot")) or _num(ind.get("close"))
    empct = _num((straddle or {}).get("straddle_pct"))
    lo, hi = _num((straddle or {}).get("lower")), _num((straddle or {}).get("upper"))
    cw, pw = _num((walls or {}).get("call_wall")), _num((walls or {}).get("put_wall"))
    grows = (gamma or {}).get("rows") or []
    glast = grows[-1] if grows else {}
    flip = _num(glast.get("gex_flip"))
    regime = glast.get("regime") or (gamma or {}).get("summary", {}).get("current_regime")
    dex = _num(glast.get("dex_total"))
    sksum = (skew or {}).get("summary") or {}
    rr = _num(sksum.get("current_rr_25d"))
    rrpc = _num(sksum.get("current_pctile_252d"))
    vrlabel = (vr or {}).get("label")
    ptilt = (flow or {}).get("tilt")
    pcr = _num((flow or {}).get("put_call_ratio"))
    nc = _num((oi or {}).get("net_call_oi_change"))
    npu = _num((oi or {}).get("net_put_oi_change"))

    # scores
    ts = _tech_score(ind)
    oq = _optq_score(vr, skew, oi)

    # EM cone marks
    marks = [(lo, "EM low", "dn"), (pw, "put wall", "dn"), (flip, "flip", "fl"),
             (spot, "spot", "sp"), (cw, "call wall", "cw"), (hi, "EM high", "up")]
    cone = _cone(spot, lo, hi, marks)

    # gamma term bars
    term = (gexterm or {}).get("term") or []
    gbars = _hbars([(str(r.get("expiration", ""))[5:], _num(r.get("gex")), "gx") for r in term[:8]])

    # KPI scorecard rows (latest vs prior)
    cur = kpis[0] if kpis else None
    prv = kpis[1] if len(kpis) > 1 else None

    def _kpi(lbl, attr, unit="%"):
        c = getattr(cur, attr, None) if cur else None
        p = getattr(prv, attr, None) if prv else None
        cs = "—" if c is None else (f"{c:g}{unit}")
        delta = ""
        if c is not None and p is not None:
            d = c - p
            cls = "up" if d >= 0 else "dn"
            delta = f' <span class="{cls}">({d:+g})</span>'
        return f"<tr><td>{lbl}</td><td>{cs}{delta}</td></tr>"

    kpi_rows = ""
    if cur:
        kpi_rows = (
            _kpi("DBNRR (net retention)", "dbnrr_pct")
            + _kpi("Revenue growth YoY", "revenue_growth_yoy_pct")
            + _kpi("Gross margin", "gross_margin_pct")
            + _kpi("cRPO growth YoY", "crpo_growth_yoy_pct")
            + _kpi("Operating margin", "operating_margin_pct")
        )
        gd = getattr(cur, "guidance_direction", None)
        one = getattr(cur, "one_line_kpi_read", None)
    else:
        gd = one = None

    # short interest
    si = shorts.get("finra_si")
    rs = shorts.get("regsho_daily")
    si_line = "—"
    if si is not None and getattr(si, "days_to_cover", None) is not None:
        si_line = f"Days-to-cover {_f(si.days_to_cover)} · SI {_f(si.short_interest,0)}"
    elif rs is not None and getattr(rs, "short_volume_ratio_avg", None) is not None:
        si_line = f"Reg SHO 10d short-vol ratio {_pct((rs.short_volume_ratio_avg or 0)*100)}"

    # research note (Hidden Angle) — strip the markdown header, cap length
    note_md = (note or {}).get("note_md") or ""
    angle = ""
    if "Uploaded research excerpt" in note_md:
        angle = note_md.split("Uploaded research excerpt", 1)[1].strip(" \n#")[:900]
    elif note_md:
        angle = note_md[:900]

    # estimates
    est_line = "—"
    if est is not None:
        parts = []
        if getattr(est, "eps_avg", None) is not None:
            parts.append(f"EPS est {_f(est.eps_avg)}")
        if getattr(est, "revenue_avg", None) is not None:
            parts.append(f"rev est {_f(est.revenue_avg,0)}")
        est_line = " · ".join(parts) or "—"

    # scenario levels
    def _lvl(v):
        return f"${_f(v,0)}" if _num(v) is not None else "—"

    rpt = ""
    if ev:
        rpt = f" · reports {_esc(ev.get('date'))} {_esc(ev.get('time') or '')}"

    crush = ""
    atm = _num((straddle or {}).get("atm_iv"))
    if atm is not None:
        crush = (f'<div class="warn">Earnings-week ATM IV ≈ {atm*100:.0f}%. Post-print vol crush is '
                 f'steep — long single-legs fight direction <i>and</i> crush; favour spreads or a post-gap entry.</div>')

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{sym} — Swing Earnings Dossier</title>
<style>
:root{{--bg:#0f1216;--panel:#171b21;--p2:#1d222a;--ink:#e7ebf0;--mut:#9aa4b2;--line:#2a313b;--ac:#4f9cf0;--ac2:#f0a02a;--gn:#39b878;--rd:#e2564a}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,Segoe UI,Roboto,Arial,sans-serif}}
.wrap{{max-width:760px;margin:0 auto;padding:20px 16px 60px}}h1{{font-size:22px;margin:0}}.tk{{color:var(--ac)}}
.sub{{color:var(--mut);font-size:13px}}h2{{font-size:14px;text-transform:uppercase;letter-spacing:.04em;margin:22px 0 8px;color:var(--ac);border-bottom:1px solid var(--line);padding-bottom:5px}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:13px 15px;margin:9px 0}}
.kv{{display:flex;justify-content:space-between;gap:10px;padding:4px 0;border-bottom:1px dashed var(--line);font-size:14px}}
.kv .k{{color:var(--mut)}}.kv b{{color:var(--ink)}}
table{{width:100%;border-collapse:collapse;font-size:13.5px}}td{{padding:5px 7px;border-bottom:1px solid var(--line)}}td:first-child{{color:var(--mut)}}
.na{{color:var(--mut);font-size:13px}}.up{{color:var(--gn)}}.dn{{color:var(--rd)}}.neu{{color:var(--ac2)}}
.warn{{background:rgba(226,86,74,.09);border-left:3px solid var(--rd);border-radius:0 8px 8px 0;padding:9px 12px;margin:8px 0;font-size:13px}}
.angle{{background:var(--p2);border-left:3px solid var(--gn);border-radius:0 8px 8px 0;padding:9px 12px;margin:8px 0;font-size:13px;white-space:pre-wrap}}
.pill{{font-size:11px;font-weight:700;padding:2px 7px;border-radius:5px;background:#232a33;color:#8cc0f5}}
svg text{{fill:var(--mut);font-size:9px}}svg text.mk{{fill:var(--ink);font-weight:700}}svg text.mv{{fill:var(--mut)}}
svg text.bl{{fill:var(--mut);font-size:10px}}svg text.bv{{fill:#cdd5df;font-size:10px}}
.axis{{stroke:#39425090;stroke-width:6;stroke-linecap:round}}.sp{{stroke:#e7ebf0;stroke-width:2;fill:#e7ebf0}}.cw{{stroke:#f0a02a;fill:#f0a02a}}
.up{{stroke:#39b878}}.dn{{stroke:#e2564a}}.fl{{stroke:#a98bf0;fill:#a98bf0}}circle.up{{fill:#39b878}}circle.dn{{fill:#e2564a}}
.gx{{fill:#4f9cf0}}
.foot{{color:var(--mut);font-size:11px;margin-top:22px;border-top:1px solid var(--line);padding-top:10px}}
</style></head><body><div class="wrap">
<h1><span class="tk">{sym}</span> — Swing Earnings Dossier</h1>
<div class="sub">auto-generated {date.today().isoformat()}{rpt} · CVForge/trading-intel · descriptive, not a signal</div>

<h2>1 · Expected move</h2>
<div class="card">
<div class="kv"><span class="k">Spot</span><b>${_f(spot)}</b></div>
<div class="kv"><span class="k">Implied move</span><b>±{_f(empct)}%</b></div>
<div class="kv"><span class="k">Straddle band</span><b>${_f(lo,0)} – ${_f(hi,0)}</b></div>
<div class="kv"><span class="k">Consensus</span><b>{_esc(est_line)}</b></div>
{crush}
<div style="margin-top:8px">{cone}</div></div>

<h2>2 · Dealer-gamma map</h2>
<div class="card">
<div class="kv"><span class="k">Net GEX / regime</span><b>{_f((gexterm or {}).get('gex_total'),0)} · {_esc(regime)}</b></div>
<div class="kv"><span class="k">Flip · Call wall · Put wall</span><b>{_lvl(flip)} · {_lvl(cw)} · {_lvl(pw)}</b></div>
<div class="kv"><span class="k">Dealer delta (DEX)</span><b>{_f(dex,0)}</b></div>
<div style="margin-top:8px"><div class="na">GEX by expiry — thin front-week = gap over-realizes then damps (EM-break)</div>{gbars}</div></div>

<h2>3 · Options positioning</h2>
<div class="card">
<div class="kv"><span class="k">25Δ skew (RR)</span><b>{_f(rr,3)} · {_esc(sksum.get('label'))}{'' if rrpc is None else f' · p{rrpc*100:.0f}'}</b></div>
<div class="kv"><span class="k">IV vs RV (VRP)</span><b>{_esc(vrlabel)}</b></div>
<div class="kv"><span class="k">Flow tilt · PCR</span><b>{_esc(ptilt)} · {_f(pcr)}</b></div>
<div class="kv"><span class="k">Fresh OI (Δcall / Δput)</span><b>{_f(nc,0)} / {_f(npu,0)}</b></div></div>

<h2>4 · Earnings KPI scorecard</h2>
<div class="card">{('<table>'+kpi_rows+'</table>') if kpi_rows else '<div class="na">No transcript KPIs banked yet (kpi_snapshots).</div>'}
{f'<div class="kv" style="margin-top:6px"><span class="k">Guidance</span><b>{_esc(gd)}</b></div>' if kpi_rows else ''}
{f'<div class="na" style="margin-top:6px">{_esc(one)}</div>' if one else ''}</div>

<h2>5 · Hidden-Angle / regime note</h2>
<div class="card">{f'<div class="angle">{_esc(angle)}</div>' if angle else '<div class="na">No research note on file.</div>'}</div>

<h2>6 · Short interest &amp; scores</h2>
<div class="card">
<div class="kv"><span class="k">Short interest</span><b>{_esc(si_line)}</b></div>
<div class="kv"><span class="k">Technical score</span><b>{'—' if ts is None else ts}</b></div>
<div class="kv"><span class="k">Option-quality score</span><b>{'—' if oq is None else oq}</b></div></div>

<h2>7 · Scenario playbook</h2>
<div class="card">
<div class="kv"><span class="k up">Bull (beat + raise)</span><b>toward {_lvl(hi)} / call wall {_lvl(cw)}</b></div>
<div class="kv"><span class="k neu">Base (in-line)</span><b>pin near flip {_lvl(flip)}; rich IV crushes</b></div>
<div class="kv"><span class="k dn">Bear (miss)</span><b>toward {_lvl(lo)} / put wall {_lvl(pw)}</b></div>
<div class="na" style="margin-top:6px">Thin front-week gamma → the gap over-realizes then the back-dated gamma damps; the higher-conviction swing is often the post-earnings EM-break → burn-off re-entry.</div></div>

<div class="foot">CVForge / trading-intel: straddle · walls · gex_term · gamma_history · skew · technicals · oi_changes · vol_richness · watchlist_flow · research_note · kpi_snapshots · short_interest_snapshots · estimate_snapshots. Blank fields = the source hasn't populated. Not investment advice; flow/skew/OI/gamma are descriptive of positioning (rule 4).</div>
</div></body></html>"""


# ── universe + orchestration ────────────────────────────────────────────────
def _week_reporters(session: Session, settings: Settings, *, days: int) -> list[dict]:
    """Watchlist names with an earnings_events date in the next ``days``."""
    today = eastern_now().date()
    horizon = today + timedelta(days=days)
    wl = {s.upper() for s in settings.watchlist_symbols}
    rows = session.execute(
        select(EarningsEvent.symbol, EarningsEvent.date, EarningsEvent.time)
        .where(EarningsEvent.date >= today, EarningsEvent.date <= horizon)
        .order_by(EarningsEvent.date)
    ).all()
    seen: set[str] = set()
    out: list[dict] = []
    for sym, d, tm in rows:
        s = (sym or "").upper()
        if s in wl and s not in seen:
            seen.add(s)
            out.append({"symbol": s, "date": d.isoformat() if d else None, "time": tm})
    return out


def run(
    session: Session,
    *,
    settings: Settings | None = None,
    symbols: list[str] | None = None,
    days: int = 8,
    telegram: TelegramClient | None = None,
) -> dict:
    """Build + Telegram-push a swing dossier per monitored name reporting soon."""
    settings = settings or get_settings()
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="weekly_swing_dossiers")
    tg = telegram if telegram is not None else TelegramClient(settings)

    if symbols:
        targets = [{"symbol": s.upper(), "date": None, "time": None} for s in symbols]
    else:
        targets = _week_reporters(session, settings, days=days)

    written: list[str] = []
    lines: list[str] = []
    for ev in targets:
        sym = ev["symbol"]
        try:
            dest = build_one(session, sym, settings=settings, ev=ev)
        except Exception as exc:  # one bad name never kills the batch
            bound.warning("swing_dossier.skip", symbol=sym, err=str(exc))
            continue
        if dest is None:
            continue
        written.append(str(dest))
        when = f" ({ev['date']} {ev['time'] or ''})".rstrip() if ev.get("date") else ""
        cap = f"{sym} swing dossier{when}"
        tg.send_document(dest, caption=cap)
        lines.append(f"• <b>{sym}</b>{when}")

    if lines and tg.enabled:
        header = f"<b>Swing earnings dossiers</b> — {len(written)} name(s) reporting this week:\n" + "\n".join(lines)
        tg.send_message(header)

    bound.info("weekly_swing_dossiers.done", n=len(written), symbols=[e["symbol"] for e in targets])
    return {"written": written}


def main() -> None:
    from trading_intel.memory.db import make_session_factory

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    settings = get_settings()
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    session_factory = make_session_factory(settings)
    with session_factory() as session:
        result = run(session, settings=settings, symbols=args or None)
    print(f"Done: {result}")


if __name__ == "__main__":
    main()
