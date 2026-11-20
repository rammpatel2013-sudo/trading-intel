"""HTML page for the leveraged-ETF close flow (2026-11-19) -> :8899/close_flow.html.

Phone rule (2026-11-14): every number and chart is written into the HTML (inline SVG), the page reads
completely with scripts off; big text, no sideways scroll at ~390 px. Refreshes itself every 60 s.
"""

from __future__ import annotations

import html
from datetime import datetime, timedelta

from trading_intel.etf_flows.close_flow import fmt_usd

E = html.escape
RED, GREEN, GREY = "#f87171", "#4ade80", "#9ca3af"
LEVEL = {"LIGHT": 0.25, "MODERATE": 0.5, "HEAVY": 0.75, "EXTREME": 1.0}


def _card(g: dict, window: str) -> str:
    d = g["direction"]
    col = RED if d == "SELL" else GREEN if d == "BUY" else GREY
    arrow = "▼" if d == "SELL" else "▲" if d == "BUY" else "•"
    if g["index_last"] is None:
        return (f'<div class="card"><div class="lab">{E(g["name"])} CLOSE FLOW</div>'
                f'<div class="big" style="color:{GREY}">no price yet today</div></div>')
    fut = abs(g["futures"] or 0)
    flip = "above" if d == "SELL" else "below"
    pct = (g["index_ret"] or 0) * 100
    fill = LEVEL.get(g["strength"], 0.25) * 100
    warn = ""
    if g["n_missing"]:
        warn += f'<div class="warn">{g["n_missing"]} fund(s) missing shares or prices - total is low</div>'
    if g["n_index_fallback"]:
        warn += f'<div class="note">{g["n_index_fallback"]} fund(s) with an old bar: used the index move x leverage</div>'
    return f"""<div class="card">
<div class="lab">{E(g["name"])} CLOSE FLOW · {E(window)}</div>
<div class="big" style="color:{col}">{arrow} {d} ≈ {fmt_usd(abs(g["rebalance_usd"]))} ≈ {fut:,.0f} {E(g["future"])}</div>
<div class="bar"><div style="width:{fill:.0f}%;background:{col}"></div></div>
<div class="row"><span class="str" style="color:{col}">{E(g["strength"])}</span>
<span>{E(g["group"])} {g["index_last"]:,.2f} ({pct:+.2f}% vs prior close)</span></div>
<div class="row"><span>flips {flip} <b>{g["flip_level"]:,.2f}</b> (prior close)</span></div>
<div class="row small"><span>{fmt_usd(g["per_1pct_usd"])} per 1% move · funds {fmt_usd(g["aum_usd"])} · price as of {E((g.get("index_ts") or "")[11:16])}</span></div>
{warn}</div>"""


def _path_svg(points: list[tuple[datetime, float]], col: str) -> str:
    if len(points) < 2:
        return '<div class="note">path appears after the second pass (every 2 min)</div>'
    w, h, pad = 360, 140, 30
    xs = [p[0].timestamp() for p in points]
    ys = [p[1] / 1e9 for p in points]
    x0, x1 = min(xs), max(xs) or 1
    lo, hi = min(min(ys), 0.0), max(max(ys), 0.0)
    span = (hi - lo) or 1.0

    def X(x: float) -> float:
        return pad + (x - x0) / ((x1 - x0) or 1) * (w - pad - 8)

    def Y(y: float) -> float:
        return 8 + (hi - y) / span * (h - 30)

    pts = " ".join(f"{X(x):.1f},{Y(y):.1f}" for x, y in zip(xs, ys))
    zero = Y(0.0)
    labels = (f'<text x="2" y="{Y(hi) + 4:.0f}">{hi:+.1f}B</text>'
              f'<text x="2" y="{Y(lo) + 4:.0f}">{lo:+.1f}B</text>'
              f'<text x="{pad}" y="{h - 4}">{points[0][0]:%H:%M}</text>'
              f'<text x="{w - 40}" y="{h - 4}">{points[-1][0]:%H:%M}</text>')
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="estimate path">'
            f'<line x1="{pad}" x2="{w - 8}" y1="{zero:.1f}" y2="{zero:.1f}" stroke="#555" stroke-dasharray="4 4"/>'
            f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="3"/>'
            f'<g fill="#cbd5e1" font-size="13">{labels}</g></svg>')


def _funds_table(g: dict) -> str:
    rows = []
    for f in g["funds"]:
        r = f["rebalance_usd"]
        col = RED if (r or 0) < 0 else GREEN
        mv = "-" if f["fund_ret"] is None else f"{f['fund_ret'] * 100:+.2f}%"
        rows.append(
            f'<tr><td>{E(f["symbol"])}</td><td>{f["leverage"]:+.0f}x</td><td>{fmt_usd(f["aum_usd"])}</td>'
            f'<td>{mv}</td>'
            f'<td style="color:{col}">{fmt_usd(r)}</td><td>{E(f["used"] or "")}</td></tr>'
        )
    return ('<table><tr><th>Fund</th><th>Lev</th><th>Size</th><th>Move</th><th>Trade</th><th>Price</th></tr>'
            + "".join(rows) + "</table>")


def render(res: dict, hist: dict[str, list[tuple[datetime, float]]]) -> str:
    close = res["close"]
    start = datetime.combine(res["day"], close) - timedelta(minutes=15)
    window = f"{start:%H:%M}–{close:%H:%M}"
    cards, paths, tables = [], [], []
    for g in res["groups"]:
        col = RED if g["direction"] == "SELL" else GREEN if g["direction"] == "BUY" else GREY
        cards.append(_card(g, window))
        paths.append(f'<h2>{E(g["group"])} estimate through the afternoon ($)</h2>' + _path_svg(hist.get(g["group"], []), col))
        tables.append(f'<h2>{E(g["group"])} funds</h2>' + _funds_table(g))
    err = ""
    if res["errors"]:
        err = '<div class="warn">price pull problems: ' + E("; ".join(res["errors"][:6])) + "</div>"
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="60">
<title>LETF close flow {res["day"]}</title><style>
body{{background:#0b0f17;color:#e5e7eb;font:18px/1.4 system-ui,sans-serif;margin:0;padding:12px;max-width:760px}}
h1{{font-size:24px;margin:4px 0 10px}} h2{{font-size:19px;margin:18px 0 6px}}
.card{{background:#151b27;border:1px solid #2a3344;border-radius:12px;padding:14px;margin:10px 0}}
.lab{{color:#cbd5e1;font-size:15px;letter-spacing:.06em;font-weight:600}}
.big{{font-size:28px;font-weight:800;margin:6px 0}}
.bar{{height:10px;background:#2a3344;border-radius:6px;overflow:hidden;margin:6px 0}} .bar div{{height:100%}}
.row{{display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px;margin:4px 0}}
.str{{font-weight:800}} .small{{font-size:15px;color:#cbd5e1}}
.warn{{color:#fbbf24;font-size:16px;margin-top:6px}} .note{{color:#cbd5e1;font-size:15px}}
table{{border-collapse:collapse;width:100%;font-size:16px}} td,th{{padding:5px 4px;border-bottom:1px solid #2a3344;text-align:right}}
td:first-child,th:first-child{{text-align:left}} th{{color:#cbd5e1}}
</style></head><body>
<h1>Leveraged-ETF close flow · {res["day"]} · {res["ts"]:%H:%M} ET</h1>
<div class="note">Leveraged and inverse funds must reset their exposure near the close. On a down day every one of
them sells, on an up day every one buys: trade = leverage × (leverage − 1) × fund size × index move. The sign
flips at the prior close. Prices are FMP 1-minute bars (can be a few minutes late). Some funds hedge with swaps
or earlier in the day, so read this as the upper estimate.</div>
{err}{"".join(cards)}{"".join(paths)}{"".join(tables)}
<div class="note">Updates every 2 min in the last hour; page reloads every 60 s.</div>
</body></html>"""
