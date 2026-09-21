"""Market breadth board — A-D line, % above 50/200DMA, new highs-lows, McClellan.

Line graphs over the banked ``breadth_snapshots`` (FMP S&P-500 constituents).
Server-side inline SVG only, no <script>, so it opens in Telegram on a phone.

Only TRADING SESSIONS are banked (``timeutils.is_trading_session`` gates the
collector). Before that gate existed, weekend runs re-banked Friday's stale
counts and cumulated them into ``ad_line`` -- 13 such rows were removed and the
line recomputed on 2026-09-21.

Descriptor only (FlashAlpha rule 4).

Run:
    python scripts/breadth_report.py [--days 120] [--no-push]
"""

from __future__ import annotations

import argparse
import datetime as dt
import html as _html
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

BG, PANEL, GRID, TXT, MUTED = "#0d1524", "#111b2e", "#1c2c5c", "#e6edf7", "#6f83bd"
CYAN, AMBER, GREEN, RED, VIOLET = "#3fc9d4", "#f59e0b", "#1f9e6e", "#9e3b4d", "#8b7cf0"


def _line(dates, series, title, sub, colors, *, zero=False) -> str:
    live = [(lab, [v for v in vals], c) for lab, vals, c in series
            if any(v is not None for v in vals)]
    if not live or len(dates) < 2:
        return f'<div class="panel"><h2>{_html.escape(title)}</h2><p class="empty">No data.</p></div>'
    W, H, pl, pr, pt, pb = 980, 260, 58, 16, 16, 34
    vals = [v for _, vs, _ in live for v in vs if v is not None]
    lo, hi = min(vals), max(vals)
    if zero:
        lo, hi = min(lo, 0.0), max(hi, 0.0)
    pad = (hi - lo) * 0.12 or 1.0
    lo, hi = lo - pad, hi + pad
    n = len(dates)
    X = [pl + (W - pl - pr) * i / max(n - 1, 1) for i in range(n)]
    Y = lambda v: pt + (H - pt - pb) * (1 - (v - lo) / (hi - lo))
    o = [f'<svg viewBox="0 0 {W} {H}" class="chart" xmlns="http://www.w3.org/2000/svg">']
    for f in (0, .25, .5, .75, 1):
        v = lo + (hi - lo) * f
        y = Y(v)
        o.append(f'<line x1="{pl}" y1="{y:.1f}" x2="{W-pr}" y2="{y:.1f}" stroke="{GRID}"/>'
                 f'<text x="{pl-6}" y="{y+3:.1f}" text-anchor="end" font-size="9" fill="{MUTED}">{v:,.0f}</text>')
    if zero and lo < 0 < hi:
        o.append(f'<line x1="{pl}" y1="{Y(0):.1f}" x2="{W-pr}" y2="{Y(0):.1f}" stroke="{MUTED}" stroke-dasharray="4 3"/>')
    for lab, vs, c in live:
        pts = [f"{X[i]:.1f},{Y(v):.1f}" for i, v in enumerate(vs) if v is not None]
        if len(pts) > 1:
            o.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{c}" stroke-width="2.2"/>')
        last = next((i for i in range(n - 1, -1, -1) if vs[i] is not None), None)
        if last is not None:
            o.append(f'<circle cx="{X[last]:.1f}" cy="{Y(vs[last]):.1f}" r="3.4" fill="{c}"/>'
                     f'<text x="{X[last]-6:.1f}" y="{Y(vs[last])-8:.1f}" text-anchor="end" font-size="10" fill="{c}">{vs[last]:,.0f}</text>')
    step = max(1, n // 7)
    for i in range(0, n, step):
        o.append(f'<text x="{X[i]:.1f}" y="{H-12}" text-anchor="middle" font-size="9" fill="{MUTED}">'
                 f'{dt.date.fromisoformat(str(dates[i])).strftime("%d %b")}</text>')
    leg = " &#183; ".join(f'<span style="color:{c}">&#9632; {_html.escape(l)}</span>' for l, _, c in live)
    return (f'<div class="panel"><h2>{_html.escape(title)}</h2>'
            f'<p class="sub2">{_html.escape(sub)}</p>{"".join(o)}</svg>'.replace("</svg></svg>", "</svg>")
            + f'<p class="leg">{leg}</p></div>')


def build(*, days: int = 120, out_path: str | None = None, settings=None) -> str:  # noqa: ANN001
    import sqlalchemy as sa

    from trading_intel.config import get_settings
    from trading_intel.memory.db import make_session_factory

    st = settings or get_settings()
    with make_session_factory(st)() as s:
        rows = s.execute(sa.text(
            "select ts::date d, advancers, decliners, net_adv, ad_line, new_highs, new_lows,"
            " pct_above_50, pct_above_200, mcclellan_osc, spx_close, divergence_state"
            " from breadth_snapshots where ts >= now() - (:n || ' days')::interval order by ts"
        ), {"n": days}).all()
    if not rows:
        raise ValueError("no breadth rows banked")
    d = [str(r[0]) for r in rows]
    f = lambda i: [(float(r[i]) if r[i] is not None else None) for r in rows]
    last = rows[-1]
    body = (
        _line(d, [("A-D line (cumulative)", f(4), CYAN)], "ADVANCE / DECLINE LINE",
              "cumulative net advancers, S&P 500 constituents", None)
        + _line(d, [("% > 50DMA", f(7), AMBER), ("% > 200DMA", f(8), VIOLET)],
                "PARTICIPATION", "share of constituents above their moving average", None)
        + _line(d, [("new highs", f(5), GREEN), ("new lows", f(6), RED)],
                "NEW HIGHS vs NEW LOWS", "52-week, daily count", None)
        + _line(d, [("McClellan osc", f(9), CYAN)], "McCLELLAN OSCILLATOR",
                "breadth momentum; zero line marked", None, zero=True)
    )
    kpis = [("A-D line", f"{float(last[4]):,.0f}"), ("% > 50DMA", f"{last[7]}%"),
            ("% > 200DMA", f"{last[8]}%"), ("new H / L", f"{last[5]} / {last[6]}"),
            ("divergence", str(last[11] or "none"))]
    chips = "".join(f'<span class="chip"><b>{_html.escape(k)}</b> {_html.escape(v)}</span>' for k, v in kpis)
    doc = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Market Breadth</title><style>
*{{box-sizing:border-box}}body{{margin:0;padding:12px;background:{BG};color:{TXT};
font:13px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}}
h1{{font-size:17px;margin:0 0 2px}}.sub{{color:{MUTED};font-size:11px;margin-bottom:12px}}
.panel{{background:{PANEL};border:1px solid {GRID};border-radius:8px;padding:12px;margin-bottom:12px}}
h2{{font-size:11px;letter-spacing:.09em;text-transform:uppercase;color:{MUTED};margin:0 0 2px}}
.sub2{{color:{MUTED};font-size:10px;margin:0 0 8px}}.chart{{width:100%;height:auto;display:block}}
.chips{{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px}}
.chip{{background:{PANEL};border:1px solid {GRID};border-radius:5px;padding:4px 8px;font-size:11px}}
.chip b{{color:{MUTED};font-weight:600;letter-spacing:.05em;font-size:9px}}
.leg{{color:{MUTED};font-size:10px;margin:6px 0 0}}.empty{{color:{MUTED}}}
.foot{{color:{MUTED};font-size:10px;margin-top:6px}}</style></head><body>
<h1>MARKET BREADTH</h1>
<div class="sub">S&amp;P 500 constituents &#183; {d[0]} &#8594; {d[-1]} &#183; {len(d)} trading sessions</div>
<div class="chips">{chips}</div>{body}
<p class="foot">Trading sessions only. Weekend/holiday rows are no longer banked
(<b>is_trading_session</b> gate); 13 stale weekend rows were removed and the cumulative
A-D line recomputed on 2026-09-21 &#8212; it had drifted ~253 points from duplicated
Friday counts. Descriptor only, not a signal (FlashAlpha rule 4).</p></body></html>"""
    out = Path(out_path) if out_path else Path("reports") / f"breadth_{dt.date.today()}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")
    return str(out)


def main() -> None:
    structlog.configure(processors=[structlog.processors.add_log_level,
                                    structlog.processors.TimeStamper(fmt="iso"),
                                    structlog.processors.JSONRenderer()])
    ap = argparse.ArgumentParser(description="Build the market-breadth board.")
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    from trading_intel.config import get_settings

    st = get_settings()
    p = build(days=a.days, out_path=a.out, settings=st)
    print(f"breadth board: {p}")
    if not a.no_push:
        from trading_intel.clients.telegram import TelegramClient

        print("telegram_sent=", TelegramClient(st).send_document(p, caption="Market Breadth - A-D line, participation, H/L, McClellan"))


if __name__ == "__main__":
    main()
