"""Mag7 report (2026-11-13 overnight; backlog #91 #92 #93). One HTML page, big visuals, hover titles.
NAS DSM Fri 17:30 ET: sudo bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_script.sh mag7_report
  1) weekly priced move vs what happened (cheap / rich options), 2) gamma by strike x expiry,
  3) 1-month ATM IV with earnings dates + call / put premium from the tape. Descriptor only (rule 4)."""
from __future__ import annotations

import argparse
import html
from datetime import date
from pathlib import Path

E = html.escape


def bars_svg(rows: list[dict], w: int = 520, h: int = 170) -> str:
    if not rows:
        return "<i>no weekly data yet</i>"
    mx = max(max(r["implied_pct"], r["realized_pct"]) for r in rows) or 1
    bw = w / len(rows)
    out = []
    for i, r in enumerate(rows):
        x = i * bw
        hi, hr = r["implied_pct"] / mx * (h - 30), r["realized_pct"] / mx * (h - 30)
        col = "#16a34a" if r["realized_pct"] > r["implied_pct"] else "#dc2626"
        out.append(f'<rect x="{x + 2:.1f}" y="{h - 20 - hi:.1f}" width="{bw * 0.45:.1f}" height="{hi:.1f}" fill="#94a3b8">'
                   f'<title>week of {E(r["week_of"])}: priced {r["implied_pct"]:.1f}%</title></rect>'
                   f'<rect x="{x + 2 + bw * 0.45:.1f}" y="{h - 20 - hr:.1f}" width="{bw * 0.45:.1f}" height="{hr:.1f}" fill="{col}">'
                   f'<title>week of {E(r["week_of"])}: moved {r["realized_pct"]:.1f}%</title></rect>')
    return f'<svg viewBox="0 0 {w} {h}" width="100%" style="max-width:{w}px">{"".join(out)}</svg>'


def heat_svg(g: dict, w: int = 520, h: int = 260) -> str:
    if not g.get("found"):
        return "<i>no option book</i>"
    ks, ex, grid = g["strikes"], g["expiries"], g["grid"]
    mx = max((abs(v) for row in grid for v in row), default=1) or 1
    cw, ch = (w - 60) / max(len(ex), 1), (h - 30) / max(len(ks), 1)
    cells = []
    for i, k in enumerate(reversed(ks)):
        row = grid[len(ks) - 1 - i]
        for j, v in enumerate(row):
            a = min(1.0, abs(v) / mx) ** 0.5
            col = f"rgba(22,163,74,{a:.2f})" if v > 0 else f"rgba(220,38,38,{a:.2f})"
            cells.append(f'<rect x="{60 + j * cw:.1f}" y="{i * ch:.1f}" width="{cw:.1f}" height="{ch:.1f}" fill="{col}">'
                         f'<title>{k:g} · {E(ex[j])}: {v:+.3g}</title></rect>')
        if i % max(1, len(ks) // 8) == 0:
            cells.append(f'<text x="2" y="{i * ch + ch:.1f}" font-size="12">{k:g}</text>')
    for j, e in enumerate(ex):
        cells.append(f'<text x="{60 + j * cw + 2:.1f}" y="{h - 8}" font-size="11">{E(e[5:])}</text>')
    return f'<svg viewBox="0 0 {w} {h}" width="100%" style="max-width:{w}px">{"".join(cells)}</svg>'


def iv_svg(d: dict, w: int = 520, h: int = 200) -> str:
    if not d.get("found"):
        return "<i>no IV history yet</i>"
    pts = d["points"]
    ivs = [p["iv"] for p in pts]
    lo, hi = min(ivs), max(ivs)
    sp = (hi - lo) or 1
    X = lambda i: 40 + i * (w - 50) / max(len(pts) - 1, 1)  # noqa: E731
    Y = lambda v: 10 + (hi - v) / sp * (h - 70)  # noqa: E731
    line = " ".join(f"{X(i):.1f},{Y(p['iv']):.1f}" for i, p in enumerate(pts))
    dates = [p["date"] for p in pts]
    marks = "".join(f'<line x1="{X(dates.index(e)):.1f}" x2="{X(dates.index(e)):.1f}" y1="5" y2="{h - 60}" stroke="#f59e0b" stroke-width="3">'
                    f'<title>earnings {E(e)}</title></line>' for e in d["earnings"] if e in dates)
    fl = d.get("flow") or {}
    mxf = max((c + p for c, p in fl.values()), default=1) or 1
    strip = "".join(
        f'<rect x="{X(i) - 3:.1f}" y="{h - 50}" width="6" height="{40 * c / mxf:.1f}" fill="#16a34a"><title>{E(p["date"])} calls ${c / 1e6:.1f}M</title></rect>'
        f'<rect x="{X(i) - 3:.1f}" y="{h - 50 + 40 * c / mxf:.1f}" width="6" height="{40 * q / mxf:.1f}" fill="#dc2626"><title>{E(p["date"])} puts ${q / 1e6:.1f}M</title></rect>'
        for i, p in enumerate(pts) for c, q in [fl.get(p["date"], (0.0, 0.0))])
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" style="max-width:{w}px"><polyline points="{line}" fill="none" stroke="#2563eb" stroke-width="3"/>'
            f'{marks}{strip}<text x="2" y="{Y(hi) + 4:.1f}" font-size="12">{hi:.0f}%</text><text x="2" y="{Y(lo) + 4:.1f}" font-size="12">{lo:.0f}%</text></svg>')


def render(data: dict[str, dict]) -> str:
    cards = []
    for sym, d in data.items():
        wk = d["weekly"]
        last = wk[-1] if wk else None
        verdict = "" if not last else (
            f'Last week moved {last["realized_pct"]:.1f}% vs {last["implied_pct"]:.1f}% priced - options were '
            + ("<b style='color:#16a34a'>cheap</b>" if last["realized_pct"] > last["implied_pct"] else "<b style='color:#dc2626'>rich</b>"))
        cards.append(f"""<div class="card"><h2>{E(sym)}</h2><div class="note">{verdict}</div>
<h3>Priced (grey) vs actual weekly move (green = moved more, red = less)</h3>{bars_svg(wk)}
<h3>Dealer gamma by strike x expiry (green +, red -; naive book)</h3>{heat_svg(d["gamma"])}
<h3>1-month ATM IV (blue), earnings (orange), call / put premium (green / red)</h3>{iv_svg(d["iv"])}</div>""")
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mag7 options {date.today()}</title><style>body{{font-family:system-ui;margin:14px;font-size:16px;color:#0f172a;background:#f8fafc}}
.card{{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:14px;margin:12px 0}} h2{{font-size:24px;margin:0}}
h3{{font-size:15px;color:#334155;margin:12px 0 4px}} .note{{font-size:17px;margin:4px 0}}</style></head><body>
<h1>Mag7 options - priced vs real moves, gamma map, IV</h1>{"".join(cards)}
<p style="color:#64748b">Implied weekly move = 0.8 x IV x sqrt(days/365) (straddle rule of thumb). Hover any mark for numbers. Descriptor, not a signal.</p></body></html>"""


def main() -> None:
    a = argparse.ArgumentParser()
    a.add_argument("--no-telegram", action="store_true")
    args = a.parse_args()
    from trading_intel.config import get_settings
    from trading_intel.market.mag7_views import MAG7, gamma_grid, iv_history, weekly_moves
    from trading_intel.memory.db import make_session_factory
    s = get_settings()
    with make_session_factory(s)() as session:
        data = {m: {"weekly": weekly_moves(session, m), "gamma": gamma_grid(session, m), "iv": iv_history(session, m)} for m in MAG7}
    out = Path("reports/mag7")
    out.mkdir(parents=True, exist_ok=True)
    f = out / f"mag7_{date.today():%Y%m%d}.html"
    f.write_text(render(data), encoding="utf-8")
    print("wrote", f)
    if not args.no_telegram:
        from trading_intel.clients.telegram import TelegramClient
        TelegramClient(s).send_document(f, caption="Mag7 options: priced vs real moves, gamma map, IV")


if __name__ == "__main__":
    main()
