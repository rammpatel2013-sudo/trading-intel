"""VOL BOARD — spot / skew30 / ATM fixed-strike vol + realized skew + CM term structure.

Ports the cvforge browser board into a Telegram-deliverable trading-intel report.
Three panels:
  1. SPOT - SKEW30 - FIXED STRIKE VOL  (one contract, re-marked day by day)
  2. REALIZED SKEW                     (implied vs realized slope, R2, lambda)
  3. TERM STRUCTURE                    (CM IV30..IV360, live vs trailing quartiles)

PHONE RULE: rendered ENTIRELY server-side - every element is a static HTML/SVG
string, NO <script>, NO CDN - so it opens in Telegram's in-app viewer. The
browser app it replaces drew its charts client-side, which is exactly why it
never rendered on mobile.

Descriptor / research only - nothing here writes to ``signals`` (rule 4).

Run:
    python scripts/vol_board_report.py TLT            # build + push
    python scripts/vol_board_report.py TLT --no-push
"""

from __future__ import annotations

import argparse
import datetime as dt
import html as _html
import math
from pathlib import Path

import numpy as np
import structlog

from trading_intel.vol import vol_board_data as D
from trading_intel.vol.vol_board import (
    MIN_R2, TENORS, classify, implied_skew, quartiles, realized_skew, skew_lambda,
)

log = structlog.get_logger(__name__)

BG, PANEL, GRID, TXT, MUTED = "#0d1524", "#111b2e", "#1c2c5c", "#e6edf7", "#6f83bd"
GREEN, RED, PINK, CYAN, VIOLET, AMBER = "#1f9e6e", "#9e3b4d", "#e0457b", "#3fc9d4", "#8b7cf0", "#f59e0b"


def _e(s) -> str:
    return _html.escape(str(s))


def _sc(vals, lo, hi, a, b):
    if hi - lo < 1e-12:
        return [(a + b) / 2 for _ in vals]
    return [a + (b - a) * (v - lo) / (hi - lo) for v in vals]


def _panel1(dates, fs_iv, spot_cum, skew30, title) -> str:
    """Bars = fixed-strike vol change from window start; lines = spot cum %, skew30 (inverted)."""
    if len(dates) < 3:
        return f'<div class="panel"><h2>{_e(title)}</h2><p class="empty">Not enough contract history.</p></div>'
    W, H, pl, pr, pt, pb = 980, 300, 54, 54, 18, 40
    fs0 = fs_iv[0]
    bars = [v - fs0 for v in fs_iv]
    lo_l = min(list(bars) + list(spot_cum)) ; hi_l = max(list(bars) + list(spot_cum))
    pad = max(0.35, (hi_l - lo_l) * 0.12); lo_l -= pad; hi_l += pad
    sk = [v for v in skew30 if v is not None and math.isfinite(v)]
    lo_r, hi_r = (min(sk), max(sk)) if sk else (0.0, 1.0)
    if hi_r - lo_r < 1e-9: hi_r = lo_r + 1.0
    rpad = (hi_r - lo_r) * 0.12; lo_r -= rpad; hi_r += rpad
    n = len(dates)
    X = [pl + (W - pl - pr) * i / max(n - 1, 1) for i in range(n)]
    Yl = lambda v: pt + (H - pt - pb) * (1 - (v - lo_l) / (hi_l - lo_l))
    Yr = lambda v: pt + (H - pt - pb) * ((v - lo_r) / (hi_r - lo_r))  # INVERTED
    bw = max(3.0, (W - pl - pr) / max(n, 1) * 0.55)
    out = [f'<svg viewBox="0 0 {W} {H}" class="chart" xmlns="http://www.w3.org/2000/svg">']
    for f in (0, .25, .5, .75, 1):
        v = lo_l + (hi_l - lo_l) * f; y = Yl(v)
        out.append(f'<line x1="{pl}" y1="{y:.1f}" x2="{W-pr}" y2="{y:.1f}" stroke="{GRID}"/>'
                   f'<text x="{pl-6}" y="{y+3:.1f}" text-anchor="end" font-size="9" fill="{MUTED}">{v:.2f}</text>')
    y0 = Yl(0.0)
    out.append(f'<line x1="{pl}" y1="{y0:.1f}" x2="{W-pr}" y2="{y0:.1f}" stroke="{PINK}" stroke-opacity=".45"/>')
    for i, b in enumerate(bars):
        y = Yl(b); h = abs(y - y0)
        out.append(f'<rect x="{X[i]-bw/2:.1f}" y="{min(y,y0):.1f}" width="{bw:.1f}" height="{max(h,0.8):.1f}" '
                   f'fill="{GREEN if b>=0 else RED}" fill-opacity=".85"/>')
    out.append('<polyline points="' + " ".join(f"{X[i]:.1f},{Yl(spot_cum[i]):.1f}" for i in range(n)) +
               f'" fill="none" stroke="#ffffff" stroke-width="2"/>')
    pts = [(X[i], Yr(skew30[i])) for i in range(n) if skew30[i] is not None and math.isfinite(skew30[i])]
    if len(pts) > 1:
        out.append('<polyline points="' + " ".join(f"{x:.1f},{y:.1f}" for x, y in pts) +
                   f'" fill="none" stroke="{PINK}" stroke-width="2"/>')
    for f in (0, .5, 1):
        v = lo_r + (hi_r - lo_r) * f; y = Yr(v)
        out.append(f'<text x="{W-pr+6}" y="{y+3:.1f}" font-size="9" fill="{PINK}">{v:.2f}</text>')
    step = max(1, n // 8)
    for i in range(0, n, step):
        out.append(f'<text x="{X[i]:.1f}" y="{H-14}" text-anchor="middle" font-size="9" fill="{MUTED}">'
                   f'{_e(dt.date.fromisoformat(dates[i]).strftime("%d %b"))}</text>')
    out.append(f'<text x="{pl}" y="{H-2}" font-size="9" fill="{MUTED}">'
               f'bars = fixed-strike vol change (pts) | white = spot cum % | pink = SKEW30 (inverted)</text></svg>')
    return f'<div class="panel"><h2>{_e(title)}</h2>{"".join(out)}</div>'


def _panel2(smile, hist_pts, im, im_r2, rs, rs_r2, lam) -> str:
    """Lambda is suppressed below MIN_R2 so an unfit slope never reads as a finding."""
    W, H, pl, pr, pt, pb = 980, 300, 54, 20, 18, 40
    chips = [("IMPLIED SKEW", f"{im:+.3f}" if im is not None else "n/a", "% per 1%"),
             ("REALIZED SKEW", f"{rs:+.3f}" if rs is not None else "n/a", "% per 1%"),
             ("REALIZED FIT R2", f"{rs_r2:.2f}" if rs_r2 is not None else "n/a", ""),
             ("LAMBDA", f"{lam:.2f}" if (lam is not None and rs_r2 is not None and rs_r2 >= MIN_R2) else "n/a",
              "realized / implied")]
    chip_html = "".join(f'<span class="chip"><b>{_e(a)}</b> {_e(b)} <i>{_e(c)}</i></span>' for a, b, c in chips)
    xs = [p[0] for p in smile] + [p[0] for p in hist_pts]
    ys = [p[1] for p in smile] + [p[1] for p in hist_pts]
    if len(xs) < 4:
        return f'<div class="panel"><h2>REALIZED SKEW</h2><div class="chips">{chip_html}</div>' \
               '<p class="empty">Not enough history for the scatter.</p></div>'
    lo_x, hi_x, lo_y, hi_y = min(xs), max(xs), min(ys), max(ys)
    hi_x += (hi_x - lo_x) * .05 or 1; lo_x -= (hi_x - lo_x) * .05
    hi_y += (hi_y - lo_y) * .1 or 1; lo_y -= (hi_y - lo_y) * .1
    X = lambda v: pl + (W - pl - pr) * (v - lo_x) / (hi_x - lo_x)
    Y = lambda v: pt + (H - pt - pb) * (1 - (v - lo_y) / (hi_y - lo_y))
    o = [f'<svg viewBox="0 0 {W} {H}" class="chart" xmlns="http://www.w3.org/2000/svg">']
    for f in (0, .25, .5, .75, 1):
        v = lo_y + (hi_y - lo_y) * f; y = Y(v)
        o.append(f'<line x1="{pl}" y1="{y:.1f}" x2="{W-pr}" y2="{y:.1f}" stroke="{GRID}"/>'
                 f'<text x="{pl-6}" y="{y+3:.1f}" text-anchor="end" font-size="9" fill="{MUTED}">{v:.1f}</text>')
    COLS = [RED, AMBER, "#8b95b5"]
    for (x, y, bucket) in hist_pts:
        o.append(f'<circle cx="{X(x):.1f}" cy="{Y(y):.1f}" r="2.6" fill="{COLS[min(bucket,2)]}" fill-opacity=".8"/>')
    if len(smile) > 1:
        o.append('<polyline points="' + " ".join(f"{X(a):.1f},{Y(b):.1f}" for a, b in sorted(smile)) +
                 f'" fill="none" stroke="{GREEN}" stroke-width="2.2"/>')
    if rs is not None and hist_pts:
        hx = [p[0] for p in hist_pts]; mx, Mx = min(hx), max(hx)
        my = float(np.mean([p[1] for p in hist_pts])); mxm = float(np.mean(hx))
        f = lambda xv: my + rs * ((xv - mxm) / mxm * 100.0)
        o.append(f'<line x1="{X(mx):.1f}" y1="{Y(f(mx)):.1f}" x2="{X(Mx):.1f}" y2="{Y(f(Mx)):.1f}" '
                 f'stroke="{RED}" stroke-width="1.8" stroke-dasharray="5 4"/>')
    for i in range(6):
        v = lo_x + (hi_x - lo_x) * i / 5
        o.append(f'<text x="{X(v):.1f}" y="{H-14}" text-anchor="middle" font-size="9" fill="{MUTED}">{v:.1f}</text>')
    o.append(f'<text x="{pl}" y="{H-2}" font-size="9" fill="{MUTED}">x = strike / spot ($) &#183; '
             f'green = current smile &#183; dots = spot vs IV30 by month (T-1/T-2/T-3) &#183; dashed = realized fit</text></svg>')
    return f'<div class="panel"><h2>REALIZED SKEW</h2><div class="chips">{chip_html}</div>{"".join(o)}</div>'


def _panel3(cur, qs) -> str:
    W, H, pl, pr, pt, pb = 980, 300, 54, 20, 18, 44
    have = [t for t in TENORS if qs.get(t) or cur.get(t) is not None]
    if not have:
        return '<div class="panel"><h2>TERM STRUCTURE</h2><p class="empty">No data.</p></div>'
    vals = []
    for t in have:
        q = qs.get(t)
        if q: vals += [q["lo"], q["hi"]]
        if cur.get(t) is not None: vals.append(cur[t] * 100)
    lo_y, hi_y = min(vals), max(vals); pad = (hi_y - lo_y) * .15 or 1; lo_y -= pad; hi_y += pad
    Y = lambda v: pt + (H - pt - pb) * (1 - (v - lo_y) / (hi_y - lo_y))
    slot = (W - pl - pr) / len(have)
    X = [pl + slot * (i + .5) for i in range(len(have))]
    bw = min(70, slot * .45)
    o = [f'<svg viewBox="0 0 {W} {H}" class="chart" xmlns="http://www.w3.org/2000/svg">']
    for f in (0, .25, .5, .75, 1):
        v = lo_y + (hi_y - lo_y) * f; y = Y(v)
        o.append(f'<line x1="{pl}" y1="{y:.1f}" x2="{W-pr}" y2="{y:.1f}" stroke="{GRID}"/>'
                 f'<text x="{pl-6}" y="{y+3:.1f}" text-anchor="end" font-size="9" fill="{MUTED}">{v:.1f}</text>')
    for i, t in enumerate(have):
        q = qs.get(t)
        if q:
            o.append(f'<line x1="{X[i]:.1f}" y1="{Y(q["hi"]):.1f}" x2="{X[i]:.1f}" y2="{Y(q["lo"]):.1f}" stroke="{VIOLET}"/>')
            for lv in ("hi", "lo"):
                o.append(f'<line x1="{X[i]-bw/4:.1f}" y1="{Y(q[lv]):.1f}" x2="{X[i]+bw/4:.1f}" y2="{Y(q[lv]):.1f}" stroke="{VIOLET}"/>')
            o.append(f'<rect x="{X[i]-bw/2:.1f}" y="{Y(q["q3"]):.1f}" width="{bw:.1f}" '
                     f'height="{max(Y(q["q1"])-Y(q["q3"]),1):.1f}" fill="{VIOLET}" fill-opacity=".22" stroke="{VIOLET}"/>')
            o.append(f'<line x1="{X[i]-bw/2:.1f}" y1="{Y(q["med"]):.1f}" x2="{X[i]+bw/2:.1f}" y2="{Y(q["med"]):.1f}" stroke="{VIOLET}" stroke-width="1.6"/>')
    live = [(X[i], cur[t] * 100) for i, t in enumerate(have) if cur.get(t) is not None]
    if len(live) > 1:
        o.append('<polyline points="' + " ".join(f"{x:.1f},{Y(v):.1f}" for x, v in live) +
                 f'" fill="none" stroke="{CYAN}" stroke-width="2.2"/>')
    for x, v in live:
        o.append(f'<circle cx="{x:.1f}" cy="{Y(v):.1f}" r="3.6" fill="{CYAN}"/>'
                 f'<text x="{x:.1f}" y="{Y(v)-9:.1f}" text-anchor="middle" font-size="10" fill="{CYAN}">{v:.1f}</text>')
    for i, t in enumerate(have):
        q = qs.get(t)
        o.append(f'<text x="{X[i]:.1f}" y="{H-24}" text-anchor="middle" font-size="10" fill="{TXT}">IV{t}</text>')
        o.append(f'<text x="{X[i]:.1f}" y="{H-11}" text-anchor="middle" font-size="8" fill="{MUTED}">'
                 f'{("n="+str(q["n"])) if q else "no range"}</text>')
    o.append(f'<text x="{pl}" y="{H-1}" font-size="9" fill="{MUTED}">boxes = trailing quartiles (min/Q1/med/Q3/max) &#183; cyan = current</text></svg>')
    return f'<div class="panel"><h2>TERM STRUCTURE (trailing range)</h2>{"".join(o)}</div>'


def build(symbol: str, *, out_path: str | None = None, settings=None, days: int = 400) -> str:  # noqa: ANN001
    from trading_intel.clients.cvforge import CVForgeClient
    from trading_intel.config import get_settings
    from trading_intel.memory.db import make_session_factory

    st = settings or get_settings()
    client = CVForgeClient(st)
    ref = dt.date.today()
    sym = symbol.upper()

    spot, rows = D.fetch_chain(client, sym)
    cur = D.current_curve(rows, spot, ref)
    hist = D.build_history(client, sym, rows, spot, ref, days=days, max_exp=8, per_exp=3)
    exp, strike, fs = "", 0.0, {}
    cands = []
    for tgt in (30, 45, 21):
        cands += D.focus_candidates(rows, spot, ref, target_dte=tgt, n=4)
    for cand_exp, cand_k in dict.fromkeys(cands):
        series = D.fixed_strike_series(client, sym, cand_exp, cand_k, ref, days=60)
        if len(series) > len(fs):
            exp, strike, fs = cand_exp, cand_k, series
        if len(fs) >= 15:
            break

    with make_session_factory(st)() as session:
        sk = D.fetch_skew30(session, sym, days=60)

    dates = sorted(set(fs) & set(hist.index)) if not hist.empty else sorted(fs)
    dates = dates[-30:] if dates else sorted(fs)[-30:]
    fs_iv = [fs[d] for d in dates]
    sp = [float(hist.loc[d, "spot"]) if (not hist.empty and d in hist.index) else math.nan for d in dates]
    s0 = next((v for v in sp if math.isfinite(v)), math.nan)
    spot_cum = [((v / s0 - 1) * 100 if math.isfinite(v) and math.isfinite(s0) else math.nan) for v in sp]
    skew_line = [sk.get(d) for d in dates]

    im, im_r2 = implied_skew(rows, spot)
    rs = rs_r2 = lam = None
    hist_pts: list[tuple[float, float, int]] = []
    if not hist.empty and 30 in hist.columns:
        h = hist.dropna(subset=[30]).tail(63)
        if len(h) > 5:
            sv = h["spot"].to_numpy(dtype=float); iv = h[30].to_numpy(dtype=float)
            ret = np.diff(sv) / sv[:-1] * 100.0
            rs, rs_r2 = realized_skew(ret, np.diff(iv))
            lam = skew_lambda(rs, im)
            m = len(h)
            for i, (d, r_) in enumerate(h.iterrows()):
                hist_pts.append((float(r_["spot"]), float(r_[30]), 0 if i >= m - 21 else (1 if i >= m - 42 else 2)))
    smile = D.otm_smile(rows, spot, exp)
    qs = {t: (quartiles(hist[t].to_numpy(dtype=float)) if (not hist.empty and t in hist.columns) else None)
          for t in TENORS}
    read = classify(lam, cur, qs, r2=rs_r2)

    label = f"{sym} {dt.date.fromisoformat(exp).strftime('%b%d')} {strike:g}"
    title = "SPOT - SKEW - FIXED STRIKE VOL \u2014 " + label
    body = (_panel1(dates, fs_iv, spot_cum, skew_line, title)
            + _panel2(smile, hist_pts, im, im_r2, rs, rs_r2, lam)
            + _panel3(cur, qs))
    span = f"{hist.index.min()} &#8594; {hist.index.max()}" if not hist.empty else "n/a"
    doc = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(sym)} Vol Board</title><style>
*{{box-sizing:border-box}}body{{margin:0;padding:12px;background:{BG};color:{TXT};
font:13px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}}
h1{{font-size:17px;margin:0 0 2px}}.sub{{color:{MUTED};font-size:11px;margin-bottom:12px}}
.panel{{background:{PANEL};border:1px solid {GRID};border-radius:8px;padding:12px;margin-bottom:12px}}
h2{{font-size:11px;letter-spacing:.09em;text-transform:uppercase;color:{MUTED};margin:0 0 10px}}
.chart{{width:100%;height:auto;display:block}}
.chips{{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px}}
.chip{{background:{BG};border:1px solid {GRID};border-radius:5px;padding:4px 8px;font-size:11px}}
.chip b{{color:{MUTED};font-weight:600;letter-spacing:.05em;font-size:9px}}
.chip i{{color:{MUTED};font-style:normal;font-size:9px}}
.read{{background:{PANEL};border-left:3px solid {CYAN};border-radius:6px;padding:10px 12px;margin-bottom:12px}}
.read b{{color:{CYAN};font-size:13px}}.read p{{margin:4px 0 0;color:#c3cee4;font-size:12px}}
.empty{{color:{MUTED};font-size:12px}}
.foot{{color:{MUTED};font-size:10px;margin-top:6px}}
</style></head><body>
<h1>{_e(sym)} &#183; VOL BOARD</h1>
<div class="sub">spot {spot:,.2f} &#183; {ref} &#183; focus {_e(exp)} {strike:g} &#183; history {span}</div>
<div class="read"><b>{_e(read.headline)}</b><p>{_e(read.narrative)}</p></div>
{body}
<p class="foot">IV solved against the put-call-parity forward (no dividend assumption).
Trailing IV rebuilt from CVForge /mas contract closes; constant-maturity legs interpolated in
total variance and never extrapolated &#8212; a tenor not bracketed that day is simply absent,
which is why box sample sizes differ. Regime <b>descriptor</b> only, not a signal (FlashAlpha rule 4).</p>
</body></html>"""
    out = Path(out_path) if out_path else Path("reports") / f"{sym}_vol_board_{ref}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")
    return str(out)


def main() -> None:
    structlog.configure(processors=[structlog.processors.add_log_level,
                                    structlog.processors.TimeStamper(fmt="iso"),
                                    structlog.processors.JSONRenderer()])
    ap = argparse.ArgumentParser(description="Build the vol board report.")
    ap.add_argument("symbol", nargs="?", default="SPY")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--out", default=None)
    ap.add_argument("--days", type=int, default=400)
    a = ap.parse_args()
    from trading_intel.config import get_settings

    st = get_settings()
    path = build(a.symbol, out_path=a.out, settings=st, days=a.days)
    print(f"vol board: {path}")
    if not a.no_push:
        from trading_intel.clients.telegram import TelegramClient

        print("telegram_sent=", TelegramClient(st).send_document(
            path, caption=f"{a.symbol.upper()} Vol Board - spot/skew30/fixed-strike vol, realized skew, CM term"))


if __name__ == "__main__":
    main()
