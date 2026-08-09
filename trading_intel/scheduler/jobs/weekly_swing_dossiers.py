"""Weekly job: rich swing-trade earnings dossier per monitored name reporting soon.

Universe = WATCHLIST ∩ forward earnings calendar (``earnings_events``) over the
next ``days``. For each name, assemble a full earnings read from the reader layer
the MCP tools use (``mcp.tools`` / ``mcp.extra_tools``), CVForge OHLC + transcript,
and the kpi / short-interest / estimate tables, render a self-contained HTML with
STATIC inline-SVG charts, rasterize a PNG, and push BOTH to Telegram.

Sections: expected move + IV-crush · weekly/4h price + Weinstein stage ·
dealer-gamma map · options positioning (+ local-LLM "how to read") · IV-vs-RV ·
notable option trade · KPI scorecard · transcript highlights + what-to-watch ·
past-earnings moves · short interest · factor scores (incl. sector) ·
scenario playbook incl. a vol-crush ("IV reverts to mean") case.

PHONE RULE (report-deploy-workflow): charts are STATIC inline SVG built here —
NO client JS, NO CDN. AI text is local Ollama (rule 7). Descriptive only (rule 4).

Manual run:
    python -m trading_intel.scheduler.jobs.weekly_swing_dossiers NET CSCO
"""

from __future__ import annotations

import html as _html
import math
import os
import re
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.clients.cvforge import CVForgeClient
from trading_intel.clients.telegram import TelegramClient
from trading_intel.config import Settings, get_settings
from trading_intel.earnings import transcript_read as tqa
from trading_intel.earnings import transcripts as tx
from trading_intel.mcp import extra_tools as et
from trading_intel.mcp import tools as t
from trading_intel.memory.models import (
    EarningsEvent,
    EstimateSnapshot,
    KpiSnapshot,
    QuoteDaily,
    ShortInterestSnapshot,
)
from trading_intel.research.stage import TIMEFRAMES, classify
from trading_intel.synthesis.llm import LLMProvider, OllamaProvider
from trading_intel.timeutils import eastern_now

log = structlog.get_logger(__name__)
_OUT = Path("reports")

# GICS sector -> SPDR proxy (for the sector-momentum score).
_SECTOR_SPDR = {
    "Technology": "XLK", "Information Technology": "XLK", "Financial Services": "XLF",
    "Financials": "XLF", "Healthcare": "XLV", "Health Care": "XLV", "Energy": "XLE",
    "Consumer Cyclical": "XLY", "Consumer Discretionary": "XLY", "Consumer Defensive": "XLP",
    "Consumer Staples": "XLP", "Industrials": "XLI", "Basic Materials": "XLB",
    "Materials": "XLB", "Utilities": "XLU", "Real Estate": "XLRE",
    "Communication Services": "XLC",
}


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


def _ai(llm, prompt, *, model=None, max_tokens=220):
    """One local-LLM completion, trimmed; '' on any failure (rule 7, degrades)."""
    if llm is None:
        return ""
    try:
        out = llm.complete(prompt, model=model, max_tokens=max_tokens)
        return _html.escape(out.strip()[:900])
    except Exception as exc:  # noqa: BLE001
        log.warning("swing_dossier.ai_failed", err=str(exc))
        return ""


# ── STATIC inline SVG (colours via CSS classes; no JS) ─────────────────────
def _cone(spot, lo, hi, marks):
    spot, lo, hi = _num(spot), _num(lo), _num(hi)
    if not (spot and lo and hi) or hi <= lo:
        return '<div class="na">expected move unavailable</div>'
    W, pad = 620, 46
    x = lambda v: round(pad + (v - lo) / (hi - lo) * (W - 2 * pad), 1)
    out = [f'<svg viewBox="0 0 {W} 70" width="100%">',
           f'<line x1="{pad}" y1="36" x2="{W-pad}" y2="36" class="axis"/>']
    placed = []
    for v, lab, cls in marks:
        v = _num(v)
        if v is None or v < lo or v > hi:
            continue
        xv = x(v)
        up = not any(abs(xv - px) < 48 for px in placed)
        placed.append(xv)
        ly, ty = (14, 24) if up else (58, 68)
        out += [f'<line x1="{xv}" y1="22" x2="{xv}" y2="50" class="{cls}"/>',
                f'<circle cx="{xv}" cy="36" r="3" class="{cls}"/>',
                f'<text x="{xv}" y="{ly}" text-anchor="middle" class="mk">{lab}</text>',
                f'<text x="{xv}" y="{ty}" text-anchor="middle" class="mv">${v:g}</text>']
    return "".join(out) + "</svg>"


def _line_svg(series, cls, *, H=54):
    vals = [v for v in (_num(s) for s in series) if v is not None]
    if len(vals) < 3:
        return None
    W, pad = 620, 6
    mn, mx = min(vals), max(vals)
    rng = (mx - mn) or 1.0
    n = len(vals)
    pts = " ".join(f"{round(pad+i/(n-1)*(W-2*pad),1)},{round(H-pad-(v-mn)/rng*(H-2*pad),1)}"
                   for i, v in enumerate(vals))
    return f'<polyline points="{pts}" class="{cls}"/>'


def _spark(series, cls="pu"):
    p = _line_svg(series, cls)
    return f'<svg viewBox="0 0 620 54" width="100%">{p}</svg>' if p else '<div class="na">no history</div>'


def _dual_spark(a, b, ca="pu", cb="bx"):
    """Two series on a shared axis (IV vs RV)."""
    av = [x for x in (_num(v) for v in a) if x is not None]
    bv = [x for x in (_num(v) for v in b) if x is not None]
    if len(av) < 3 and len(bv) < 3:
        return '<div class="na">no IV/RV history</div>'
    allv = av + bv
    mn, mx = min(allv), max(allv)
    rng = (mx - mn) or 1.0
    W, H, pad = 620, 60, 6

    def poly(vals, cls):
        if len(vals) < 3:
            return ""
        n = len(vals)
        pts = " ".join(f"{round(pad+i/(n-1)*(W-2*pad),1)},{round(H-pad-(v-mn)/rng*(H-2*pad),1)}"
                       for i, v in enumerate(vals))
        return f'<polyline points="{pts}" class="{cls}"/>'

    return f'<svg viewBox="0 0 {W} {H}" width="100%">{poly(av,ca)}{poly(bv,cb)}</svg>'


def _price_svg(closes, ma_window):
    closes = [_num(c) for c in closes if _num(c) is not None]
    if len(closes) < 6:
        return '<div class="na">price history unavailable</div>'
    W, H, pad = 620, 150, 10
    lo, hi = min(closes), max(closes)
    rng = (hi - lo) or 1.0
    n = len(closes)
    ma = [None if i + 1 < ma_window else sum(closes[i + 1 - ma_window:i + 1]) / ma_window for i in range(n)]
    x = lambda i: round(pad + i / (n - 1) * (W - 2 * pad), 1)
    y = lambda v: round(H - pad - (v - lo) / rng * (H - 2 * pad), 1)

    def path(vals):
        pts = [f"{x(i)},{y(v)}" for i, v in enumerate(vals) if v is not None]
        return ("M" + " L".join(pts)) if pts else ""

    lx, ly = x(n - 1), y(closes[-1])
    return (f'<svg viewBox="0 0 {W} {H}" width="100%">'
            f'<path d="{path(ma)}" class="ma"/><path d="{path(closes)}" class="px"/>'
            f'<circle cx="{lx}" cy="{ly}" r="3" class="pxd"/>'
            f'<text x="{lx-4}" y="{ly-6}" text-anchor="end" class="mk">{closes[-1]:.2f}</text></svg>')


def _candles(ohlc, ma_window, *, show=60):
    """Static candlestick SVG (wick + body) with an MA overlay. No JS."""
    bars = []
    for row in (ohlc or []):
        o, h, l, c = _num(row[0]), _num(row[1]), _num(row[2]), _num(row[3])
        if None not in (o, h, l, c):
            bars.append((o, h, l, c))
    if len(bars) < 6:
        return '<div class="na">price history unavailable</div>'
    closes_full = [b[3] for b in bars]
    ma_full = [None if i + 1 < ma_window else sum(closes_full[i + 1 - ma_window:i + 1]) / ma_window
               for i in range(len(bars))]
    bars_s, ma_s = bars[-show:], ma_full[-show:]
    W, H, pad = 620, 150, 10
    hi = max(b[1] for b in bars_s)
    lo = min(b[2] for b in bars_s)
    rng = (hi - lo) or 1.0
    n = len(bars_s)
    bw = (W - 2 * pad) / n
    y = lambda v: round(H - pad - (v - lo) / rng * (H - 2 * pad), 1)
    out = [f'<svg viewBox="0 0 {W} {H}" width="100%">']
    for i, (o, h, l, c) in enumerate(bars_s):
        cx = round(pad + (i + 0.5) * bw, 1)
        cls = "cu" if c >= o else "cd"
        top, bot = y(max(o, c)), y(min(o, c))
        bh = max(1.0, bot - top)
        bwid = max(1.6, bw * 0.62)
        out += [f'<line x1="{cx}" y1="{y(h)}" x2="{cx}" y2="{y(l)}" class="{cls}" stroke-width="1"/>',
                f'<rect x="{round(cx-bwid/2,1)}" y="{top}" width="{round(bwid,1)}" height="{round(bh,1)}" class="r{cls}"/>']
    mp = [f"{round(pad+(i+0.5)*bw,1)},{y(v)}" for i, v in enumerate(ma_s) if v is not None]
    if mp:
        out.append(f'<polyline points="{" ".join(mp)}" class="ma"/>')
    out.append(f'<text x="{W-4}" y="14" text-anchor="end" class="mk">{bars_s[-1][3]:.2f}</text></svg>')
    return "".join(out)


def _gamma_insight(spot, flip, front_gex):
    spot, flip = _num(spot), _num(flip)
    if spot is None or flip is None:
        return ""
    base = ("Spot is ABOVE the flip → long-gamma: dealer hedging dampens normal-day moves and price tends to pin "
            "toward the walls." if spot > flip else
            "Spot is BELOW the flip → short-gamma: dealer hedging amplifies moves, so momentum can feed on itself.")
    fg = _num(front_gex)
    if fg is not None and fg <= 0:
        base += (" Front-week gamma is thin/negative, so the earnings gap can over-realize before the back-dated "
                 "gamma re-asserts and damps it — the classic EM-break → burn-off path.")
    return base


def _pos_insight(sksum, vr, nc, npu):
    bits = []
    bias = (sksum or {}).get("bias")
    if bias:
        bits.append("the 25Δ skew is call-heavy (upside-chasing — the crowd is paying up for calls)"
                    if "call" in str(bias).lower() else
                    "the 25Δ skew is put-heavy (protection bid — the market is paying for downside insurance)")
    lab = (vr or {}).get("label") or ""
    if "rich" in lab:
        bits.append("IV is rich, so the move is largely priced in — a beat may already be in the stock, "
                    "and long premium is expensive (favour spreads)")
    elif "cheap" in lab:
        bits.append("IV is cheap vs realized, so the move looks under-priced — long options are favoured")
    nc, npu = _num(nc), _num(npu)
    if nc is not None and npu is not None:
        bits.append("fresh OI is building in calls (bullish repositioning)" if nc > npu
                    else "fresh OI is building in puts (defensive hedging into the print)")
    if not bits:
        return ""
    s = "; ".join(bits)
    return s[0].upper() + s[1:] + "."


_STANCE_CLS = {"direct": "up", "confident": "up", "hedged": "neu", "cautious": "neu", "evasive": "dn"}


def _polcls(v):
    v = _num(v)
    return "neu" if v is None else "up" if v > 0.15 else "dn" if v < -0.15 else "neu"


def _pol_word(p):
    p = _num(p)
    if p is None:
        return "—"
    return "constructive" if p > 0.15 else "negative" if p < -0.15 else "balanced"


def _tone_panel(tr_read):
    """Multi-engine tone grid: LM polarity, QoQ inflection, Q&A-vs-prepared, guidance, FinBERT."""
    if tr_read is None:
        return ""
    infl = tr_read.inflection
    tp, tq, fb = tr_read.tone_prepared, tr_read.tone_qa, tr_read.finbert
    icls = "up" if infl.score > 0 else "dn" if infl.score < 0 else "neu"
    rows = [
        f'<div class="kv"><span class="k">Call tone (LM polarity)</span>'
        f'<b class="{_polcls(tp.polarity)}">{tp.polarity:+.2f} · {_pol_word(tp.polarity)}</b></div>',
        f'<div class="kv"><span class="k">QoQ inflection</span>'
        f'<b class="{icls}">{_esc(infl.label)}'
        + (f' · Δtone {infl.tone_delta:+.2f}' if infl.tone_delta is not None else ' · no prior')
        + '</b></div>',
    ]
    if tq is not None:
        rows.append(f'<div class="kv"><span class="k">Q&amp;A vs prepared</span>'
                    f'<b class="{_polcls(tq.polarity)}">Q&amp;A {tq.polarity:+.2f}</b>'
                    f' <span class="mut">vs prep {tp.polarity:+.2f}</span></div>')
    rows.append(f'<div class="kv"><span class="k">Uncertainty density</span>'
                f'<b>{tp.uncertainty_density*100:.1f}%</b></div>')
    gsig = infl.guidance_signal
    gword = "raise cues" if gsig > 0 else "cut cues" if gsig < 0 else "none detected"
    rows.append(f'<div class="kv"><span class="k">Guidance cues</span>'
                f'<b class="{_polcls(gsig)}">{gword}</b></div>')
    if fb:
        rows.append(f'<div class="kv"><span class="k">FinBERT (Q&amp;A · n={fb["n"]})</span>'
                    f'<b><span class="up">{fb["positive"]*100:.0f}%+</span> / '
                    f'{fb["neutral"]*100:.0f}%0 / <span class="dn">{fb["negative"]*100:.0f}%−</span></b></div>')
    else:
        rows.append('<div class="kv"><span class="k">FinBERT</span>'
                    '<b class="mut">n/a (lexicon engine only)</b></div>')
    eng = tp.engine.replace("loughran-mcdonald", "Loughran-McDonald").replace("stage-1", "Stage-1 lexicon")
    return ('<div class="grid">' + "".join(rows) + '</div>'
            f'<div class="mut" style="margin-top:6px">Tone engine: {eng}. The Q&amp;A carries more signal than the '
            'scripted remarks — an upbeat prepared read with a defensive Q&amp;A is the classic tell.</div>')


def _qa_list(qa):
    """The material analyst exchanges: topic · analyst, question, answer, stance chip."""
    if not qa:
        return ""
    out = ['<div class="cap" style="margin-top:10px">Q&amp;A — what analysts pressed on &amp; how it was answered</div>']
    for r in qa:
        st = r.get("stance") or ""
        chip = f'<span class="chip {_STANCE_CLS.get(st, "neu")}">{_esc(st)}</span>' if st else ""
        head = " · ".join(x for x in (_esc(r.get("topic") or ""), _esc(r.get("analyst") or "")) if x)
        out.append(f'<div class="qa"><div class="qh">{head} {chip}</div>'
                   f'<div class="qq"><b>Q</b> {_esc(r.get("question"))}</div>'
                   f'<div class="qan"><b>A</b> {_esc(r.get("answer"))}</div></div>')
    return "".join(out)


def _hbars(items, unit=""):
    items = [(l, _num(v), c) for l, v, c in items if _num(v) is not None]
    if not items:
        return '<div class="na">no data</div>'
    mx = max(abs(v) for _, v, _ in items) or 1.0
    W, rowh, pad = 620, 22, 66
    out = [f'<svg viewBox="0 0 {W} {rowh*len(items)+6}" width="100%">']
    for i, (lab, v, cls) in enumerate(items):
        yy = i * rowh + 4
        w = round(abs(v) / mx * (W - pad - 66), 1)
        out += [f'<text x="0" y="{yy+13}" class="bl">{_esc(lab)}</text>',
                f'<rect x="{pad}" y="{yy+3}" width="{w}" height="12" rx="2" class="{cls}"/>',
                f'<text x="{pad+w+5}" y="{yy+13}" class="bv">{v:g}{unit}</text>']
    return "".join(out) + "</svg>"


def _movebars(moves):
    """Signed % bars centred on a zero line (past-earnings reactions)."""
    moves = [(l, _num(v)) for l, v in moves if _num(v) is not None]
    if not moves:
        return '<div class="na">no past-earnings history</div>'
    mx = max(abs(v) for _, v in moves) or 1.0
    W, bw, base = 620, 620 / max(len(moves), 1), 42
    out = [f'<svg viewBox="0 0 {W} 74" width="100%"><line x1="0" y1="{base}" x2="{W}" y2="{base}" class="axis0"/>']
    for i, (lab, v) in enumerate(moves):
        cx = bw * i + bw / 2
        h = round(abs(v) / mx * 30, 1)
        up = v >= 0
        yy = base - h if up else base
        cls = "up" if up else "dn"
        out += [f'<rect x="{cx-13:.1f}" y="{yy}" width="26" height="{h}" rx="2" class="{cls}"/>',
                f'<text x="{cx:.1f}" y="{(yy-3) if up else (yy+h+11):.1f}" text-anchor="middle" class="bv">{v:+.0f}%</text>',
                f'<text x="{cx:.1f}" y="70" text-anchor="middle" class="bl">{_esc(lab)}</text>']
    return "".join(out) + "</svg>"


def _scorebars(scores):
    rows = [(l, v) for l, v in scores if v is not None]
    if not rows:
        return '<div class="na">scores unavailable</div>'
    W, rowh = 620, 26
    out = [f'<svg viewBox="0 0 {W} {rowh*len(rows)+4}" width="100%">']
    for i, (lab, v) in enumerate(rows):
        yy = i * rowh + 4
        cls = "sg" if v >= 67 else "sb" if v >= 50 else "sa" if v >= 38 else "sr"
        w = round(v / 100 * (W - 150), 1)
        out += [f'<text x="0" y="{yy+15}" class="bl">{_esc(lab)}</text>',
                f'<rect x="118" y="{yy+4}" width="{W-150}" height="13" rx="3" class="trk"/>',
                f'<rect x="118" y="{yy+4}" width="{w}" height="13" rx="3" class="{cls}"/>',
                f'<text x="{W-24}" y="{yy+15}" text-anchor="end" class="bv {cls}t">{int(v)}</text>']
    return "".join(out) + "</svg>"


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


def _fund_score(cur):
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


def _ret_pct(closes, lookback):
    closes = [c for c in closes if c is not None]
    if len(closes) <= lookback:
        return None
    return (closes[-1] / closes[-1 - lookback] - 1) * 100


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


def _daily_closes(session, sym, *, days=520):
    rows = session.execute(
        select(QuoteDaily.date, QuoteDaily.close, QuoteDaily.rv20)
        .where(QuoteDaily.symbol == sym).order_by(QuoteDaily.date)).all()
    return rows[-days:]


def _past_earnings_moves(session, client, sym):
    """Next-day % reaction on the last few report dates (transcript dates × OHLCV)."""
    try:
        quarters = tx.available_quarters(client, sym)[:6]
    except Exception:
        return []
    rows = _daily_closes(session, sym)
    if not rows or not quarters:
        return []
    dates = [r.date for r in rows]
    closes = [_num(r.close) for r in rows]
    out = []
    for q in reversed(quarters):  # oldest→newest for the chart
        raw = q.get("date")
        try:
            ed = date.fromisoformat(str(raw)[:10])
        except (ValueError, TypeError):
            continue
        # nearest trading day on/after the report date, vs the prior close
        j = next((i for i, d in enumerate(dates) if d >= ed), None)
        if j is None or j == 0:
            continue
        prev, post = closes[j - 1], closes[j]
        if prev and post:
            lab = f"Q{q.get('quarter')}'{str(q.get('fiscalYear'))[2:]}"
            out.append((lab, (post / prev - 1) * 100))
    return out[-6:]


# ── assemble one name ────────────────────────────────────────────────────────
def build_one(session, sym, *, settings, client, llm=None, ev=None):
    sym = sym.upper()
    d = {}
    d["straddle"] = et.get_straddle(session, sym)
    d["walls"] = et.get_walls(session, sym)
    d["gexterm"] = et.get_gex_term(session, sym)
    d["gamma"] = t.get_gamma_history(session, sym, days=5)
    d["skew"] = t.get_skew_history(session, sym, horizon_dte=30, days=252)
    d["tech"] = t.get_technicals(session, sym, days=210)
    d["oi"] = et.get_oi_changes(session, sym, top=8)
    for key, fn in (("vr", lambda: et.get_vol_richness(session, [sym], settings=settings)),
                    ("flow", lambda: t.get_watchlist_flow(session, [sym], settings=settings))):
        try:
            r = fn()
            d[key] = (r.get("rows") or [{}])[0] if r else {}
        except Exception:
            d[key] = {}
    try:
        d["note"] = et.get_research_note(session, sym)
    except Exception:
        d["note"] = {}
    d["kpis"] = _latest_kpis(session, sym)
    d["shorts"] = _latest_short(session, sym)
    d["est"] = _latest_estimate(session, sym)

    # CVForge OHLC (weekly / 4h) + Weinstein stages + RV series + past moves
    d["stages"], d["px"] = {}, {}
    to = date.today().isoformat()
    frm = (date.today() - timedelta(days=1500)).isoformat()
    for tf, (mult, span, maw) in TIMEFRAMES.items():
        ohlc, closes = [], []
        try:
            df = client.aggs(sym, frm=frm, to=to, multiplier=mult, timespan=span, limit=50000)
            if df is not None and not df.empty:
                ohlc = list(zip(df["o"].tolist(), df["h"].tolist(), df["l"].tolist(), df["c"].tolist()))
                closes = [float(c) for c in df["c"].tolist()]
        except Exception:
            ohlc, closes = [], []
        if closes:
            d["stages"][tf] = classify(closes, ma_window=maw)
            d["px"][tf] = (ohlc, maw)
    drows = _daily_closes(session, sym)
    d["rv_series"] = [_num(r.rv20) for r in drows][-252:] if drows else []
    d["daily_ret3m"] = _ret_pct([_num(r.close) for r in drows], 63) if drows else None
    d["past_moves"] = _past_earnings_moves(session, client, sym)
    d["sector"] = _sector(session, client, sym, settings)
    d["transcript"] = _transcript_text(client, sym)

    if not (d["straddle"] and d["straddle"].get("found")) and not (d["tech"] and d["tech"].get("found")) and not d["px"]:
        return None, None
    html_doc = _render(sym, ev, d, llm, settings)
    _OUT.mkdir(parents=True, exist_ok=True)
    dest = _OUT / f"{sym}_swing_{date.today().isoformat()}.html"
    dest.write_text(html_doc, encoding="utf-8")
    return dest, _caption(sym, ev, d)


def _sector(session, client, sym, settings):
    """Sector name (FMP profile) + a 3-month sector-SPDR momentum score."""
    out = {"name": None, "spdr": None, "score": None}
    try:
        prof = client.fmp("profile", {"symbol": sym})
        name = (prof[0].get("sector") if isinstance(prof, list) and prof else None)
    except Exception:
        name = None
    out["name"] = name
    spdr = _SECTOR_SPDR.get(name or "")
    out["spdr"] = spdr
    if spdr:
        try:
            df = client.aggs(spdr, frm=(date.today() - timedelta(days=140)).isoformat(),
                             to=date.today().isoformat(), multiplier=1, timespan="day", limit=5000)
            closes = [float(c) for c in df["c"].tolist()] if df is not None and not df.empty else []
            r3 = _ret_pct(closes, 63)
            if r3 is not None:
                out["score"] = int(_clamp(50 + r3 * 3, 0, 100))
        except Exception:
            pass
    return out


def _transcript_text(client, sym):
    """Newest transcript + the prior quarter's TEXT (for the QoQ tone delta)."""
    try:
        recs = tx.latest_two(client, sym)
    except Exception:
        recs = []
    if not recs:
        return None
    r0 = recs[0]
    prior = recs[1].get("content") if len(recs) > 1 and isinstance(recs[1], dict) else None
    y, q = r0.get("year"), r0.get("quarter") or r0.get("period")
    label = f"Q{q} {y}" if (y and q) else "latest call"
    return {"label": label, "text": r0.get("content") or "", "prior_text": prior}


def _caption(sym, ev, d):
    st, vr, sk = d["straddle"] or {}, d["vr"] or {}, (d["skew"] or {}).get("summary") or {}
    when = f" · {ev['date']} {ev.get('time') or ''}".rstrip() if ev and ev.get("date") else ""
    parts = [f"<b>{sym}</b> swing dossier{when}", f"Expected move ±{_f(st.get('straddle_pct'))}%"]
    if vr.get("label"):
        parts.append("IV " + vr["label"].split("(")[0].strip())
    if sk.get("label"):
        parts.append("Skew: " + sk["label"])
    return "\n".join(parts)


def _render(sym, ev, d, llm, settings):
    st = d["straddle"] or {}
    walls = d["walls"] or {}
    gexterm = d["gexterm"] or {}
    glast = ((d["gamma"] or {}).get("rows") or [{}])[-1]
    skew = d["skew"] or {}
    sksum = skew.get("summary") or {}
    ind = (d["tech"] or {}).get("indicators") or {}
    oi = d["oi"] or {}
    vr = d["vr"] or {}
    flow = d["flow"] or {}
    kpis, shorts, est, sec = d["kpis"], d["shorts"], d["est"], d["sector"] or {}
    model = getattr(settings, "LLM_TAGGING_MODEL", None)

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
    cur = kpis[0] if kpis else None
    prv = kpis[1] if len(kpis) > 1 else None

    # ── charts
    cone = _cone(spot, lo, hi, [(lo, "EM low", "dn"), (pw, "put wall", "dn"), (flip, "flip", "fl"),
                                (spot, "spot", "sp"), (cw, "call wall", "cw"), (hi, "EM high", "up")])
    iv_series = [r.get("atm_iv") for r in (skew.get("rows") or [])][-40:]
    ivspark = _spark(iv_series)
    ivrv = _dual_spark(iv_series, d["rv_series"])
    gbars = _hbars([(str(r.get("expiration", ""))[5:], _num(r.get("gex")), "gx") for r in (gexterm.get("term") or [])[:8]])
    oibars = (_hbars([("Δ calls", nc, "up"), ("Δ puts", npu, "dn")])
              if (nc is not None or npu is not None) else '<div class="na">no fresh OI</div>')
    movebars = _movebars(d["past_moves"])

    def _stage_rows():
        lbl = {"weekly": "vs 30-wk MA", "daily": "vs 150-d MA", "4h": "vs ~150p (4h)"}
        out = ""
        for tf in ("weekly", "daily", "4h"):
            r = d["stages"].get(tf)
            if r is None:
                out += f'<tr><td><b>{tf}</b></td><td colspan="2" class="na">no data</td></tr>'
            else:
                out += (f'<tr><td><b>{tf}</b><div class="mut">{lbl[tf]}</div></td>'
                        f'<td><b>{_esc(r.stage)}</b> · {_esc(r.label)}</td><td class="mut">{_esc(r.action)}</td></tr>')
        return out

    scores = [("Technical", _tech_score(ind)), ("Momentum", _mom_score(ind)),
              ("Option-quality", _optq_score(vr, skew, oi)), ("Fundamental", _fund_score(cur)),
              ("Sector", sec.get("score"))]
    scorebars = _scorebars(scores)

    def _kpi(lbl, attr, note=""):
        c = _num(getattr(cur, attr, None)) if cur else None
        p = _num(getattr(prv, attr, None)) if prv else None
        cs = "—" if c is None else f"{c:g}%"
        delta = "" if (c is None or p is None) else f' <span class="{"up" if c-p>=0 else "dn"}">({c-p:+g})</span>'
        return f'<tr><td>{lbl}</td><td>{cs}{delta}</td><td class="mut">{note}</td></tr>'

    kpi_html = '<div class="na">No transcript KPIs banked yet (run kpi_snapshots).</div>'
    guide = one = None
    if cur:
        kpi_html = ("<table>"
                    + _kpi("DBNRR (net retention)", "dbnrr_pct", "the swing factor")
                    + _kpi("Revenue growth YoY", "revenue_growth_yoy_pct", "re-accel vs decel")
                    + _kpi("Gross margin", "gross_margin_pct", "compression = bear")
                    + _kpi("cRPO growth YoY", "crpo_growth_yoy_pct", "leading indicator")
                    + _kpi("Operating margin", "operating_margin_pct", "Rule-of-40") + "</table>")
        guide = getattr(cur, "guidance_direction", None)
        one = getattr(cur, "one_line_kpi_read", None)

    # ── notable option trade (biggest single-strike OI change)
    notable = "—"
    tops = sorted((oi.get("rows") or []), key=lambda r: abs(_num(r.get("oi_change")) or 0), reverse=True)
    if tops:
        r0 = tops[0]
        side = "call" if str(r0.get("cp", "")).upper().startswith("C") else "put"
        notable = (f"{'+' if (_num(r0.get('oi_change')) or 0)>=0 else ''}{int(_num(r0.get('oi_change')) or 0):,} "
                   f"OI in the {r0.get('expiry')} {_lvl(r0.get('strike'))} {side} "
                   f"(now {int(_num(r0.get('oi')) or 0):,} OI)")

    # ── research note (Hidden Angle)
    note_md = (d["note"] or {}).get("note_md") or ""
    angle = ""
    if "Uploaded research excerpt" in note_md:
        angle = note_md.split("Uploaded research excerpt", 1)[1].strip(" \n#")[:900]
    elif note_md:
        angle = note_md.replace("### ", "").strip()[:900]

    # ── AI: how-to-read the positioning + transcript highlights (local Ollama)
    pos_ctx = (f"Ticker {sym} into earnings. 25-delta skew RR {rr} label {sksum.get('label')} "
               f"(bias {sksum.get('bias')}); IV-vs-RV {vr.get('label')}; fresh OI net calls {nc} vs puts {npu}; "
               f"gamma regime {regime}, flip {flip}, spot {spot}, call wall {cw}, put wall {pw}.")
    ai_pos = _ai(llm, "In 2-3 plain sentences, explain to a swing trader HOW TO READ this options "
                 "positioning and what it implies for the earnings move. Be specific, descriptive, not advice:\n"
                 + pos_ctx, model=model)
    ai_tx = ""
    tr = d["transcript"]
    tread = None
    if tr and tr.get("text"):
        ai_tx = _ai(llm, "From this earnings-call transcript, give 3 short bullet takeaways (prefix each with '• ') "
                    "and one line 'Watch next quarter:'. Numbers where stated. No preamble.\n\n"
                    + tr["text"][:14000], model=model, max_tokens=300)
        try:
            tread = tqa.analyze(sym, tr["text"], tr.get("prior_text"), llm, model=model, n_qa=6)
        except Exception as exc:  # noqa: BLE001 — descriptive extra; never break the dossier
            log.warning("swing_dossier.transcript_read_failed", err=str(exc))
    tone_panel = _tone_panel(tread)
    qa_html = _qa_list(tread.qa if tread else [])

    # ── short interest
    si, rs = shorts.get("finra_si"), shorts.get("regsho_daily")
    si_line = "—"
    if si is not None and _num(getattr(si, "days_to_cover", None)) is not None:
        si_line = f"Days-to-cover {_f(si.days_to_cover)} · SI {_f(si.short_interest,0)}"
    elif rs is not None and _num(getattr(rs, "short_volume_ratio_avg", None)) is not None:
        si_line = f"Reg SHO 10d short-vol ratio {_f((rs.short_volume_ratio_avg or 0)*100)}%"

    est_line = "—"
    if est is not None:
        pp = [x for x in (f"EPS est {_f(est.eps_avg)}" if _num(getattr(est, 'eps_avg', None)) is not None else None,
                          f"rev est {_f(est.revenue_avg,0)}" if _num(getattr(est, 'revenue_avg', None)) is not None else None) if x]
        est_line = " · ".join(pp) or "—"

    # ── vol-crush scenario: earnings IV vs a mean baseline
    base_iv = _num(vr.get("fcst_rv"))
    crush_html = '<div class="na">insufficient IV/RV data for a crush estimate.</div>'
    if atm is not None and base_iv:
        crush_pct = _clamp((1 - base_iv / atm) * 100, 0, 99) if atm > 0 else 0
        post_move = empct * (base_iv / atm) if (empct and atm) else None
        crush_html = (
            f'<div class="kv"><span class="k">Earnings-week ATM IV</span><b>{atm*100:.0f}%</b></div>'
            f'<div class="kv"><span class="k">Mean-revert baseline (fcst RV)</span><b>{base_iv*100:.0f}%</b></div>'
            f'<div class="kv"><span class="k">Implied IV crush</span><b class="dn">≈{crush_pct:.0f}%</b></div>'
            + (f'<div class="kv"><span class="k">Post-crush 1σ move</span><b>±{post_move:.1f}%</b> '
               f'<span class="mut">(vs ±{empct:.1f}% priced now)</span></div>' if post_move else "")
            + '<div class="mut" style="margin-top:6px">If IV reverts to the RV baseline right after the print: '
              '<span class="up">Bull</span> — a long call can still LOSE if the up-move &lt; the vega bleed; you '
              'need a move beyond the post-crush band, so favour a call <i>spread</i> (sells the crush). '
              '<span class="neu">Base</span> — the crush does the work; premium sellers win, price drifts to the flip. '
              '<span class="dn">Bear</span> — puts also crush, so a bought put needs the down-move to outrun the vol '
              'collapse; a put <i>spread</i> or a post-gap short carries better.</div>')

    reads = []
    if vr.get("label"):
        reads.append("rich vol" if "rich" in vr["label"] else "cheap vol" if "cheap" in vr["label"] else "neutral vol")
    if sksum.get("bias"):
        reads.append("upside-chasing skew" if "call" in str(sksum["bias"]).lower() else "protective skew")
    if nc is not None and npu is not None:
        reads.append("fresh calls building" if nc > npu else "fresh puts building")
    oneread = " · ".join(reads) if reads else "positioning read below"
    rpt = f" · reports {_esc(ev.get('date'))} {_esc(ev.get('time') or '')}" if ev and ev.get("date") else ""

    def sect(px_tf):
        c = d["px"].get(px_tf)
        return _candles(c[0], c[1]) if c else '<div class="na">no data</div>'

    # deterministic insight (always present) + IV cycle position
    ginsight = _gamma_insight(spot, flip, (gexterm.get("term") or [{}])[0].get("gex"))
    pinsight = _pos_insight(sksum, vr, nc, npu)
    iv12 = [v for v in (_num(r.get("atm_iv")) for r in (skew.get("rows") or [])) if v is not None]
    ivrank = _num(vr.get("iv_rank"))
    if ivrank is not None:
        ivpct = ivrank * 100
    elif len(iv12) >= 10:
        ivpct = (iv12[-1] - min(iv12)) / ((max(iv12) - min(iv12)) or 1) * 100
    else:
        ivpct = None
    if ivpct is None:
        cyc_line = "IV cycle position unavailable (short history)."
    else:
        where = "the TOP of its range" if ivpct >= 70 else "the BOTTOM of its range" if ivpct <= 30 else "mid-range"
        tail = (" Rich + high in the cycle → expect a sharp post-print crush; sell vol / spreads." if ivpct >= 60
                else " Low in the cycle → the move looks cheap to own outright." if ivpct <= 35 else "")
        cyc_line = f"IV sits at the {ivpct:.0f}th percentile of its ~12-month range — {where}.{tail}"
    ivrv = _dual_spark(iv12, d["rv_series"])

    HOW = ("<b>Skew (25Δ RR)</b>: negative = calls bid over puts (upside-chasing); positive = puts bid "
           "(protection/fear); the percentile says how extreme vs its own history. "
           "<b>VRP</b>: IV rich → the move is expensive to own (sell vol / spreads); cheap → long options favoured. "
           "<b>Fresh OI</b>: calls opening + puts covering = bullish repositioning; the reverse = hedging. "
           "<b>Gamma</b>: long-gamma damps normal days but a thin front-week lets the earnings gap over-realize, then damp.")

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{sym} — Swing Earnings Dossier</title>
<style>
:root{{--bg:#0f1216;--panel:#171b21;--p2:#1d222a;--ink:#e7ebf0;--mut:#9aa4b2;--line:#2a313b;--ac:#4f9cf0;--ac2:#f0a02a;--gn:#39b878;--rd:#e2564a;--pu:#a98bf0}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,Segoe UI,Roboto,Arial,sans-serif}}
.wrap{{max-width:800px;margin:0 auto;padding:20px 16px 60px}}h1{{font-size:22px;margin:0}}.tk{{color:var(--ac)}}
.sub{{color:var(--mut);font-size:13px}}.lead{{font-size:14px;margin:10px 0}}
h2{{font-size:13px;text-transform:uppercase;letter-spacing:.05em;margin:22px 0 8px;color:var(--ac);border-bottom:1px solid var(--line);padding-bottom:5px}}h2 span{{color:var(--mut)}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:11px;padding:13px 15px;margin:9px 0}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:7px}}
.kv{{display:flex;justify-content:space-between;gap:10px;padding:4px 0;border-bottom:1px dashed var(--line);font-size:13.5px}}.kv .k{{color:var(--mut)}}.kv b{{color:var(--ink)}}
table{{width:100%;border-collapse:collapse;font-size:13px}}td{{padding:5px 7px;border-bottom:1px solid var(--line);vertical-align:top}}td:first-child{{color:var(--mut)}}
.mut{{color:var(--mut);font-size:12px}}.na{{color:var(--mut);font-size:13px}}.up{{color:var(--gn)}}.dn{{color:var(--rd)}}.neu{{color:var(--ac2)}}
.cap{{font-size:11px;color:var(--mut);margin:6px 0 2px}}.leg{{font-size:11px;color:var(--mut)}}.leg .i{{color:var(--pu)}}.leg .r{{color:var(--ac)}}
.warn{{background:rgba(226,86,74,.09);border-left:3px solid var(--rd);border-radius:0 8px 8px 0;padding:9px 12px;margin:8px 0;font-size:13px}}
.ai{{background:var(--p2);border-left:3px solid var(--ac);border-radius:0 8px 8px 0;padding:9px 12px;margin:8px 0;font-size:13px;white-space:pre-wrap}}
.angle{{background:var(--p2);border-left:3px solid var(--gn);border-radius:0 8px 8px 0;padding:9px 12px;margin:8px 0;font-size:13px;white-space:pre-wrap}}
.qa{{background:var(--p2);border:1px solid var(--line);border-radius:8px;padding:8px 11px;margin:6px 0}}
.qh{{font-size:12px;color:var(--ac);font-weight:600;margin-bottom:3px}}
.qq{{font-size:13px;margin:2px 0}}.qq b{{color:var(--mut)}}.qan{{font-size:13px;margin:2px 0;color:#cdd5df}}.qan b{{color:var(--gn)}}
.chip{{display:inline-block;font-size:10px;text-transform:uppercase;letter-spacing:.04em;padding:1px 7px;border-radius:20px;border:1px solid var(--line);vertical-align:middle}}
.chip.up{{color:var(--gn);border-color:#2c5a44}}.chip.dn{{color:var(--rd);border-color:#5a2f2c}}.chip.neu{{color:var(--ac2);border-color:#5a4a2c}}
svg text{{fill:var(--mut);font-size:9px}}svg text.mk{{fill:var(--ink);font-weight:700}}svg text.mv{{fill:var(--mut)}}svg text.bl{{fill:var(--mut);font-size:10.5px}}svg text.bv{{fill:#cdd5df;font-size:10.5px}}
.axis{{stroke:#39425090;stroke-width:6;stroke-linecap:round}}.axis0{{stroke:#39425080;stroke-width:1}}
line.sp,circle.sp{{stroke:#e7ebf0;fill:#e7ebf0;stroke-width:2}}line.cw,circle.cw{{stroke:#f0a02a;fill:#f0a02a}}line.up,circle.up{{stroke:#39b878;fill:#39b878}}line.dn,circle.dn{{stroke:#e2564a;fill:#e2564a}}line.fl,circle.fl{{stroke:#a98bf0;fill:#a98bf0}}line.cu{{stroke:#39b878}}line.cd{{stroke:#e2564a}}
rect.gx{{fill:#4f9cf0}}rect.up{{fill:#39b878}}rect.dn{{fill:#e2564a}}rect.trk{{fill:#232a33}}rect.sg{{fill:#39b878}}rect.sb{{fill:#4f9cf0}}rect.sa{{fill:#f0a02a}}rect.sr{{fill:#e2564a}}rect.rcu{{fill:#39b878}}rect.rcd{{fill:#e2564a}}
text.sgt{{fill:#39b878}}text.sbt{{fill:#8cc0f5}}text.sat{{fill:#f0a02a}}text.srt{{fill:#e2564a}}
polyline.pu{{fill:none;stroke:#a98bf0;stroke-width:2}}polyline.bx{{fill:none;stroke:#4f9cf0;stroke-width:1.6}}circle.pud{{fill:#a98bf0}}
path.ma{{fill:none;stroke:#f0a02a;stroke-width:1.2;stroke-dasharray:4 3}}path.px{{fill:none;stroke:#4f9cf0;stroke-width:1.6}}circle.pxd{{fill:#4f9cf0}}polyline.ma{{fill:none;stroke:#f0a02a;stroke-width:1.1;stroke-dasharray:4 3}}
.foot{{color:var(--mut);font-size:11px;margin-top:22px;border-top:1px solid var(--line);padding-top:10px}}
</style></head><body><div class="wrap">
<h1><span class="tk">{sym}</span> — Swing Earnings Dossier</h1>
<div class="sub">auto-generated {date.today().isoformat()}{rpt} · CVForge / trading-intel · sector: {_esc(sec.get('name'))}</div>
<div class="lead"><b>Read:</b> {_esc(oneread)}.</div>

<h2>1 · Expected move <span>· what's priced</span></h2>
<div class="card">
<div class="grid">
<div class="kv"><span class="k">Spot</span><b>${_f(spot)}</b></div><div class="kv"><span class="k">Implied move</span><b>±{_f(empct)}%</b></div>
<div class="kv"><span class="k">Straddle band</span><b>${_f(lo,0)}–${_f(hi,0)}</b></div><div class="kv"><span class="k">Consensus</span><b>{_esc(est_line)}</b></div>
</div>
{f'<div class="warn">Earnings-week ATM IV ≈ {atm*100:.0f}%. Steep post-print crush — see the vol-crush scenario below.</div>' if atm else ''}
<div style="margin-top:8px">{cone}</div><div class="cap">IV into the print (30-day ATM)</div>{ivspark}</div>

<h2>2 · Price &amp; stage <span>· weekly + 4h</span></h2>
<div class="card">
<div class="cap">Weekly (price + 30-wk MA)</div>{sect("weekly")}
<div class="cap">4-hour (price + ~150p MA)</div>{sect("4h")}
<table style="margin-top:8px"><tr><td class="mut">Timeframe</td><td class="mut">Weinstein stage</td><td class="mut">Read</td></tr>{_stage_rows()}</table></div>

<h2>3 · Dealer-gamma map <span>· the path</span></h2>
<div class="card">
<div class="grid">
<div class="kv"><span class="k">Net GEX</span><b>{_f(gexterm.get('gex_total'),0)}</b></div><div class="kv"><span class="k">Regime</span><b>{_esc(regime)}</b></div>
<div class="kv"><span class="k">Flip</span><b>{_lvl(flip)}</b></div><div class="kv"><span class="k">Call / Put wall</span><b>{_lvl(cw)} / {_lvl(pw)}</b></div>
<div class="kv"><span class="k">Dealer delta (DEX)</span><b>{_f(dex,0)}</b></div>
</div>
<div class="cap">GEX by expiry — thin front-week = gap over-realizes, then damps (EM-break)</div>{gbars}
{f'<div class="mut" style="margin-top:6px"><b>Meaning:</b> {ginsight}</div>' if ginsight else ''}</div>

<h2>4 · Options positioning <span>· how to read it</span></h2>
<div class="card">
<div class="kv"><span class="k">25Δ skew (RR)</span><b>{_f(rr,3)} · {_esc(sksum.get('label'))}{'' if rrpc is None else f' · p{rrpc*100:.0f}'}</b></div>
<div class="kv"><span class="k">IV vs RV (VRP)</span><b>{_esc(vr.get('label'))}</b></div>
<div class="kv"><span class="k">Flow tilt · PCR</span><b>{_esc(flow.get('tilt'))} · {_f(flow.get('put_call_ratio'))}</b></div>
<div class="cap">Fresh OI (day-over-day Δ by side)</div>{oibars}
{f'<div class="mut" style="margin-top:6px"><b>Meaning:</b> {pinsight}</div>' if pinsight else ''}
{f'<div class="ai"><b>AI read:</b> {ai_pos}</div>' if ai_pos else ''}
<div class="mut" style="margin-top:6px">{HOW}</div></div>

<h2>5 · IV vs realized vol <span>· where in the cycle</span></h2>
<div class="card">{ivrv}
<div class="leg"><span class="i">— IV (30-day ATM, ~12 mo)</span> &nbsp; <span class="r">— RV (20-day realized)</span></div>
<div class="mut" style="margin-top:6px"><b>Cycle:</b> {cyc_line}</div>
<div class="mut" style="margin-top:2px">IV above RV = vol-risk-premium (options rich); the gap collapses into the print, then crushes after.</div></div>

<h2>6 · Notable option trade <span>· worth flagging</span></h2>
<div class="card"><div class="kv"><span class="k">Biggest fresh OI</span><b>{_esc(notable)}</b></div></div>

<h2>7 · Earnings KPI scorecard <span>· grade the print</span></h2>
<div class="card">{kpi_html}
{f'<div class="kv" style="margin-top:6px"><span class="k">Guidance</span><b>{_esc(guide)}</b></div>' if cur else ''}
{f'<div class="na" style="margin-top:6px">{_esc(one)}</div>' if one else ''}</div>

<h2>8 · Transcript read <span>· tone · Q&amp;A · what to watch</span></h2>
<div class="card">
{f'<div class="cap">{_esc(tr["label"] if tr else "")} — tone read (3 engines)</div>{tone_panel}' if tone_panel else ('<div class="na">No transcript on file.</div>' if not (ai_tx or angle) else '')}
{qa_html}
{f'<div class="cap" style="margin-top:10px">AI highlights</div><div class="ai">{ai_tx}</div>' if ai_tx else ''}
{f'<div class="cap" style="margin-top:8px">Hidden-Angle note</div><div class="angle">{_esc(angle)}</div>' if angle else ''}
<div class="mut" style="margin-top:8px"><b>What to look for:</b> net-retention tone (bottoming vs guiding lower), the margin bridge (temporary AI-investment vs structural), guidance confidence, and whether AI/product is converting to <i>dollars</i>, not just usage.</div></div>

<h2>9 · Past-earnings reactions <span>· realized moves</span></h2>
<div class="card">{movebars}<div class="mut" style="margin-top:4px">Next-session % move on the last reports — the reality check vs the ±{_f(empct)}% now implied.</div></div>

<h2>10 · Short interest &amp; institutional</h2>
<div class="card"><div class="kv"><span class="k">Short interest</span><b>{_esc(si_line)}</b></div>
<div class="mut" style="margin-top:4px">13F / Form-4 via edgartools = roadmap (FMP premium-gates them).</div></div>

<h2>11 · Factor scores</h2>
<div class="card">{scorebars}
<div class="mut" style="margin-top:6px">Heuristic composites (0–100) from positioning + KPI + sector-SPDR momentum — not the deployed factor model.</div></div>

<h2>12 · Scenario playbook <span>· incl. vol-crush</span></h2>
<div class="card">
<div class="kv"><span class="k up">Bull (beat + raise)</span><b>toward {_lvl(hi)} / call wall {_lvl(cw)}</b></div>
<div class="kv"><span class="k neu">Base (in-line)</span><b>pin near flip {_lvl(flip)}</b></div>
<div class="kv"><span class="k dn">Bear (miss)</span><b>toward {_lvl(lo)} / put wall {_lvl(pw)}</b></div>
<div class="cap" style="margin-top:8px">Vol-crush case — IF IV reverts to its mean right after the print:</div>
{crush_html}</div>

<div class="foot">Sources: CVForge/trading-intel — straddle · walls · gex_term · gamma_history · skew · technicals · oi_changes · vol_richness · watchlist_flow · research_note · aggs(weekly/4h) · transcript · kpi/short_interest/estimate tables. AI text = local Ollama (rule 7). Blank = source not populated. Not investment advice; descriptive of positioning (rule 4).</div>
</div></body></html>"""


# ── PNG preview (best-effort) ───────────────────────────────────────────────
def _rasterize(html_path):
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None
    png = Path(html_path).with_suffix(".png")
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(args=["--no-sandbox"])
            pg = b.new_page(viewport={"width": 800, "height": 1400})
            pg.goto("file://" + os.path.abspath(str(html_path)))
            pg.wait_for_timeout(500)
            pg.screenshot(path=str(png), full_page=True)
            b.close()
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


def run(session, *, settings=None, symbols=None, days=8, client=None, llm=None, telegram=None):
    settings = settings or get_settings()
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="weekly_swing_dossiers")
    tg = telegram if telegram is not None else TelegramClient(settings)
    own_client = client is None
    client = client or CVForgeClient(settings)
    if llm is None:
        try:
            llm = OllamaProvider(settings)
        except Exception:
            llm = None

    targets = ([{"symbol": s.upper(), "date": None, "time": None} for s in symbols]
               if symbols else _week_reporters(session, settings, days=days))
    written, lines = [], []
    try:
        for ev in targets:
            sym = ev["symbol"]
            try:
                dest, caption = build_one(session, sym, settings=settings, client=client, llm=llm, ev=ev)
            except Exception as exc:  # noqa: BLE001
                bound.warning("swing_dossier.skip", symbol=sym, err=str(exc))
                continue
            if dest is None:
                continue
            written.append(str(dest))
            png = _rasterize(dest)
            sent_img = tg.send_photo(png, caption=caption) if png is not None else False
            tg.send_document(dest, caption=("" if sent_img else _plain(caption)))
            lines.append(f"• <b>{sym}</b>" + (f" ({ev['date']})" if ev.get("date") else ""))
    finally:
        if own_client:
            client.close()

    if lines and tg.enabled:
        tg.send_message(f"<b>Swing earnings dossiers</b> — {len(written)} name(s) this week:\n" + "\n".join(lines))
    bound.info("weekly_swing_dossiers.done", n=len(written), symbols=[e["symbol"] for e in targets])
    return {"written": written}


def main():
    from trading_intel.memory.db import make_session_factory

    structlog.configure(processors=[
        structlog.contextvars.merge_contextvars, structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"), structlog.processors.JSONRenderer()])
    settings = get_settings()
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    session_factory = make_session_factory(settings)
    with session_factory() as session:
        result = run(session, settings=settings, symbols=args or None)
    print(f"Done: {result}")


if __name__ == "__main__":
    main()
