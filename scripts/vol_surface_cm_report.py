"""^SPX Volatility Surface Changes — constant-maturity delta-vol board.

Canonical generator + CLI. Reads ``vol_surface_cm`` (today + a prior compare
date, ~weekly) via ``get_vol_surface_cm`` and the pure assembly/read in
``market.vol_surface_cm``. Renders the vol-surface-changes board: the delta×rung
IV grid (heatmap), the weekly vol-CHANGE grid (heatmap), the front-rung skew,
the ATM term structure + forward vol, and the auto READ banner.

The rungs are constant-maturity (7/14/21/30/60/90d) so the horizon rolls forward
on its own and the change is always same-horizon. Exposed via
``trading_intel.reports.build_vol_surface_cm``. Server-side render (no <script>,
no CDN) so it opens in Telegram / on a phone. Descriptor only (FlashAlpha rule 4).

Run:
    python scripts/vol_surface_cm_report.py            # build + push to Telegram
    python scripts/vol_surface_cm_report.py --no-push  # build only
    python scripts/vol_surface_cm_report.py QQQ
"""

from __future__ import annotations

import sys
from pathlib import Path

import structlog

from trading_intel.market.vol_surface_cm import build_view

log = structlog.get_logger(__name__)

_DEFAULT_SYMBOL = "SPX"

_READ_COLOR = {
    "rally-confirmed": "#2fbf71",
    "rally-unconfirmed": "#e0a23a",
    "fear": "#e0524a",
    "quiet-slide": "#d1495b",
    "no-read": "#7a8699",
}


# ── data ──────────────────────────────────────────────────────────────────────
def _collect(session, symbol: str):
    from trading_intel.mcp.extra_tools import get_vol_surface_cm

    d = get_vol_surface_cm(session, symbol=symbol, compare_sessions=5)
    if not d.get("found"):
        return None
    return build_view(d.get("rows_now") or [], d.get("rows_prior") or [])


# ── color helpers ─────────────────────────────────────────────────────────────
def _lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _iv_color(v, lo, hi):
    if v is None:
        return "#0e1b30"
    t = 0.0 if hi <= lo else max(0.0, min(1.0, (v - lo) / (hi - lo)))
    c = _lerp((26, 58, 90), (224, 160, 32), t)  # cool navy -> warm amber
    return f"rgb({c[0]},{c[1]},{c[2]})"


def _chg_color(v):
    if v is None:
        return "#0e1b30"
    m = max(0.0, min(1.0, abs(v) / 1.5))  # saturate at 1.5 vol pts
    base = (16, 27, 48)
    tgt = (47, 158, 113) if v > 0 else (209, 73, 91)
    c = _lerp(base, tgt, m)
    return f"rgb({c[0]},{c[1]},{c[2]})"


def _fmt(v, d=2, dash="&#8212;"):
    return f"{v:.{d}f}" if isinstance(v, (int, float)) else dash


# ── smile row order (matches the board: puts 5&#8594;50 top, calls 47.5&#8594;5 bottom) ──
def _smile_rows(deltas):
    puts = [("put", d) for d in sorted(deltas)]
    calls = [("call", d) for d in sorted([d for d in deltas if d < 50.0], reverse=True)]
    return puts + calls


def _row_label(side, d):
    if d == 50.0:
        return "50&#916; ATM"
    return f"{_fmt(d,1)}&#916; {'P' if side=='put' else 'C'}"


# ── svg ───────────────────────────────────────────────────────────────────────
def _skew_svg(view, rung):
    deltas = sorted(view.deltas)
    now = [(d, view.iv_now.get((rung, d, "put"))) for d in deltas] + \
          [(d, view.iv_now.get((rung, d, "call"))) for d in sorted(deltas, reverse=True) if d < 50.0]
    xs = list(range(len(now)))
    ys = [v for _, v in now]
    pts = [(i, y) for i, y in zip(xs, ys) if y is not None]
    if len(pts) < 2:
        return '<div style="color:#7a8699;font-size:12px">no front-rung data</div>'
    w, h, pl, pr, pt, pb = 380, 150, 34, 10, 12, 20
    yv = [y for _, y in pts]
    lo, hi = min(yv), max(yv)
    rng = (hi - lo) or 1
    X = lambda i: pl + i * (w - pl - pr) / (len(now) - 1)
    Y = lambda y: pt + (hi - y) / rng * (h - pt - pb)
    dd = "M" + " L".join(f"{X(i):.1f},{Y(y):.1f}" for i, y in pts)
    s = [f'<svg viewBox="0 0 {w} {h}" width="100%" xmlns="http://www.w3.org/2000/svg" font-family="ui-sans-serif,system-ui,sans-serif">']
    for yy in (hi, lo):
        s.append(f'<text x="{pl-4}" y="{Y(yy)+3:.1f}" fill="#7a8699" font-size="8.5" text-anchor="end">{yy:.1f}</text>')
    # change bars under the curve
    for i, (side, d) in enumerate([("put", d) for d in deltas] + [("call", d) for d in sorted(deltas, reverse=True) if d < 50.0]):
        c = view.iv_chg.get((rung, d, side))
        if c is None:
            continue
        bh = max(-14, min(14, c * 9))
        col = "#2f9e6f" if c > 0 else "#d1495b"
        s.append(f'<rect x="{X(i)-2:.1f}" y="{(h-pb) - (bh if bh>0 else 0):.1f}" width="4" height="{abs(bh):.1f}" fill="{col}" opacity="0.75"/>')
    s.append(f'<path d="{dd}" fill="none" stroke="#8fd0ff" stroke-width="2" stroke-linejoin="round"/>')
    s.append(f'<text x="{pl:.1f}" y="{h-6}" fill="#7a8699" font-size="8.5">put wing</text>')
    s.append(f'<text x="{w-pr:.1f}" y="{h-6}" fill="#7a8699" font-size="8.5" text-anchor="end">call wing</text>')
    s.append('</svg>')
    return "".join(s)


def _term_svg(view):
    rungs = view.rungs
    now = [view.atm_now.get(r) for r in rungs]
    prior = [view.atm_prior.get(r) for r in rungs]
    allv = [v for v in now + prior + list(view.fwd_now.values()) if v is not None]
    if len(allv) < 2 or len(rungs) < 2:
        return '<div style="color:#7a8699;font-size:12px">term structure banks forward</div>'
    w, h, pl, pr, pt, pb = 380, 150, 34, 10, 12, 22
    lo, hi = min(allv), max(allv)
    rng = (hi - lo) or 1
    X = lambda i: pl + i * (w - pl - pr) / (len(rungs) - 1)
    Y = lambda y: pt + (hi - y) / rng * (h - pt - pb)

    def path(vals, dash=""):
        p = [(i, y) for i, y in enumerate(vals) if y is not None]
        if len(p) < 2:
            return ""
        d = "M" + " L".join(f"{X(i):.1f},{Y(y):.1f}" for i, y in p)
        return f'<path d="{d}" fill="none" stroke="{dash}"/>'

    s = [f'<svg viewBox="0 0 {w} {h}" width="100%" xmlns="http://www.w3.org/2000/svg" font-family="ui-sans-serif,system-ui,sans-serif">']
    for yy in (hi, lo):
        s.append(f'<text x="{pl-4}" y="{Y(yy)+3:.1f}" fill="#7a8699" font-size="8.5" text-anchor="end">{yy:.1f}</text>')
    # forward vol (between rungs), plotted at the mid index
    fwd_pts = []
    for k, (a, b) in enumerate(zip(rungs, rungs[1:])):
        fv = view.fwd_now.get((a, b))
        if fv is not None:
            fwd_pts.append((k + 0.5, fv))
    if len(fwd_pts) >= 2:
        d = "M" + " L".join(f"{X(i):.1f},{Y(y):.1f}" for i, y in fwd_pts)
        s.append(f'<path d="{d}" fill="none" stroke="#c78bff" stroke-width="1.6" stroke-dasharray="3 3"/>')
    pn = path(prior)
    if pn:
        s.append(pn.replace('stroke=""', 'stroke="#7a8699" stroke-width="1.6" stroke-dasharray="4 3"'))
    pl2 = path(now)
    if pl2:
        s.append(pl2.replace('stroke=""', 'stroke="#e0a23a" stroke-width="2.2"'))
    for i, r in enumerate(rungs):
        lab = view.near_expiry.get(r)
        txt = str(lab)[5:] if lab else f"{r}d"
        s.append(f'<text x="{X(i):.1f}" y="{h-7}" fill="#7a8699" font-size="8" text-anchor="middle">{txt}</text>')
    s.append('</svg>')
    return "".join(s)


# ── render ────────────────────────────────────────────────────────────────────
def _render_html(view, symbol: str) -> str:
    if view is None or not view.rungs:
        return (f"<html><body style='background:#0a1428;color:#cdd6e6;font-family:sans-serif;padding:24px'>"
                f"<h2>{symbol} Vol Surface (CM)</h2><p>No banked constant-maturity surface yet — "
                f"the collector banks forward daily. Come back after 2+ sessions.</p></body></html>")
    rungs = view.rungs
    deltas = sorted(view.deltas)
    rows = _smile_rows(deltas)
    ivs = [v for v in view.iv_now.values() if v is not None]
    lo, hi = (min(ivs), max(ivs)) if ivs else (0, 1)

    # header
    spot_now, spot_prior = view.spot_now, view.spot_prior
    schg = ((spot_now / spot_prior - 1) * 100) if (spot_now and spot_prior) else None
    schg_s = (f'{schg:+.2f}%' if schg is not None else '&#8212;')
    schg_col = '#2fbf71' if (schg or 0) >= 0 else '#e0524a'
    rlabel = view.read_label.replace("-", " ").upper()
    rcol = _READ_COLOR.get(view.read_label, "#7a8699")

    def col_head():
        cells = '<th class="rh">DELTA</th>'
        for r in rungs:
            lab = view.near_expiry.get(r)
            sub = (str(lab) if lab else f"{r}d")
            cells += f'<th>{r}d<div class="exp">{sub}</div></th>'
        return f"<tr>{cells}</tr>"

    def surf_body():
        out = ""
        for side, d in rows:
            tds = f'<td class="rh">{_row_label(side,d)}</td>'
            for r in rungs:
                v = view.iv_now.get((r, d, side))
                tds += f'<td style="background:{_iv_color(v,lo,hi)}">{_fmt(v,1)}</td>'
            out += f"<tr>{tds}</tr>"
        return out

    def chg_body():
        out = ""
        for side, d in rows:
            tds = f'<td class="rh">{_row_label(side,d)}</td>'
            for r in rungs:
                v = view.iv_chg.get((r, d, side))
                tds += f'<td style="background:{_chg_color(v)}">{("" if v is None else format(v,"+.2f"))}</td>'
            out += f"<tr>{tds}</tr>"
        return out

    front = rungs[0]
    front_lab = view.near_expiry.get(front) or f"{front}d"

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{symbol} Volatility Surface Changes</title>
<style>
body{{margin:0;background:#0a1428;color:#cdd6e6;font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;font-size:14px;line-height:1.5}}
.wrap{{max-width:1000px;margin:0 auto;padding:20px 16px 60px}}
h1{{font-size:19px;margin:0;letter-spacing:.02em}}
.hd{{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;border-bottom:1px solid #1c2b45;padding-bottom:10px;margin-bottom:12px}}
.hd .sp{{font-size:20px;font-weight:800}}.mut{{color:#7a8699;font-size:12.5px}}
.read{{border-left:4px solid {rcol};background:#101f36;border-radius:0 10px 10px 0;padding:11px 14px;margin:12px 0}}
.read b{{color:{rcol};letter-spacing:.03em}}
h2{{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:#8fa0bd;margin:20px 0 7px;font-weight:700}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}
table{{border-collapse:collapse;width:100%;font-size:10.5px;font-variant-numeric:tabular-nums}}
th,td{{padding:3px 5px;text-align:right;border:1px solid #0a1428}}
thead th{{background:#13233c;color:#8fa0bd;font-size:9.5px;font-weight:700;text-align:right}}
th.rh,td.rh{{text-align:left;background:#0e1b30;color:#aebbd0;white-space:nowrap;font-weight:600}}
.exp{{font-size:8px;color:#6f7d95;font-weight:400}}
td{{color:#0a1428;font-weight:700}}
.panel{{background:#0d1a2f;border:1px solid #1c2b45;border-radius:10px;padding:12px 13px}}
.legend{{font-size:11px;color:#7a8699;margin-top:8px}}
.k{{display:inline-block;width:11px;height:11px;border-radius:2px;vertical-align:-1px;margin:0 3px}}
.foot{{color:#6f7d95;font-size:11.5px;border-top:1px solid #1c2b45;margin-top:22px;padding-top:12px}}
code{{background:#13233c;border-radius:4px;padding:1px 5px;font-size:11px}}
</style></head><body><div class="wrap">
<div class="hd">
  <h1>{symbol} &#183; VOLATILITY SURFACE CHANGES</h1>
  <span class="sp">{_fmt(spot_now,2)}</span>
  <span style="color:{schg_col};font-weight:700">{schg_s}</span>
  <span class="mut">current {view.ts_now or '&#8212;'} &#183; prior {view.ts_prior or '&#8212;'} (&#8776;weekly) &#183; constant-maturity</span>
</div>

<div class="read"><b>READ &#183; {rlabel}</b><br>{view.read_text}</div>

<h2>Vol surface &#183; IV by delta &#215; rung (constant-maturity)</h2>
<div class="panel"><table><thead>{col_head()}</thead><tbody>{surf_body()}</tbody></table>
<div class="legend">warmer = higher IV. Rows = the smile: OTM puts (top) &#8594; 50&#916; ATM &#8594; OTM calls (bottom). Columns = fixed forward horizons, labeled with the nearest real expiry.</div></div>

<h2>Vol CHANGES &#183; vs prior (weekly), vol points</h2>
<div class="panel"><table><thead>{col_head()}</thead><tbody>{chg_body()}</tbody></table>
<div class="legend"><span class="k" style="background:#2f9e6f"></span>marked up (bid) &#183; <span class="k" style="background:#d1495b"></span>marked down (offered). Same horizon vs a week ago &#8212; a real re-mark, not delta drift.</div></div>

<div class="grid2">
  <div><h2>Front-rung skew ({front}d &#183; {front_lab})</h2><div class="panel">{_skew_svg(view, front)}
    <div class="legend">Blue = today's smile; bars = the weekly change per delta.</div></div></div>
  <div><h2>Term structure &#183; ATM + forward vol</h2><div class="panel">{_term_svg(view)}
    <div class="legend"><span class="k" style="background:#e0a23a"></span>ATM now &#183; <span class="k" style="background:#7a8699"></span>prior &#183; <span class="k" style="background:#c78bff"></span>forward vol (between rungs).</div></div></div>
</div>

<p class="foot">Source &#8212; <code>get_vol_surface_cm({symbol})</code> over banked <code>vol_surface_cm</code>. Constant-maturity rungs roll forward automatically (today's 90d &#8776; the ~quarterly expiry; a month on it rolls). Change window &#8776; 5 sessions. Descriptor only (FlashAlpha rule 4) &#8212; the read is a regime label, not a trade signal.</p>
</div></body></html>"""


def build(symbol: str = _DEFAULT_SYMBOL, *, out_path: str | None = None, settings: object = None, session: object = None) -> str:
    from trading_intel.config import get_settings

    settings = settings or get_settings()
    sym = symbol.strip().upper()
    if session is not None:
        view = _collect(session, sym)
    else:
        from trading_intel.memory.db import make_session_factory

        with make_session_factory(settings)() as s:
            view = _collect(s, sym)
    html = _render_html(view, sym)
    out = (Path(out_path) if out_path else Path("reports") / f"vol_surface_cm_{sym}.html").resolve()
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
    parser = argparse.ArgumentParser(description="Build the CM vol-surface-changes report.")
    parser.add_argument("symbol", nargs="?", default=_DEFAULT_SYMBOL)
    parser.add_argument("--no-push", action="store_true")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    from trading_intel.config import get_settings

    settings = get_settings()
    path = build(args.symbol, out_path=args.out, settings=settings)
    print(f"vol_surface_cm report: {path}")
    if not args.no_push:
        from trading_intel.clients.telegram import TelegramClient

        sent = TelegramClient(settings).send_document(path, caption=f"{args.symbol.upper()} Volatility Surface Changes")
        print(f"telegram_sent={sent}")


if __name__ == "__main__":
    main()
