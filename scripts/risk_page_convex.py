"""risk_page_convex.py - created 2026-11-22. The Risk-page card "Where options look mispriced" (appetite group):
the top 4 names of each convexity list from convexity.json (scheduler/jobs/convexity.py) and a link to the full
Convexity page (scripts/convexity_page.py). Static HTML only. Display only (rule 4).
"""
from __future__ import annotations

from risk_page_html import esc

COLS = {"cheap_up": ("Cheap upside", "#2F7D4F", "calls cheap, rising stock"),
        "cheap_down": ("Cheap downside", "#B23A2E", "puts cheap, falling stock"),
        "rich_sell": ("Rich premium", "#3D63C9", "options dear, strong stock"),
        "avoid": ("Avoid selling", "#9A6A12", "moves bigger than priced")}
CONVEX_CSS = (".cx{display:grid;grid-template-columns:1fr 1fr;gap:8px 14px} .cx div{border-top:3px solid;padding-top:4px}"
              ".cx b{font-size:14px} .cx small{display:block;color:#6B7079;font-size:12px}"
              ".cx ul{list-style:none;margin:4px 0 0;padding:0;font-size:13px} .cx li{display:flex;justify-content:space-between;gap:6px}"
              ".cx li span{color:#6B7079;font-variant-numeric:tabular-nums} .cxl{margin-top:8px;font-size:13px}")


def _v(p: dict) -> str:
    ivr, carry = p.get("iv_rank"), p.get("carry")
    a = "" if ivr is None else f"rank {100 * ivr:.0f}%"
    b = "" if carry is None else f"{100 * carry:+.0f} pts"
    return " · ".join(x for x in (a, b) if x)


def convex_body(o: dict, n: int = 4) -> str:
    picks = o.get("picks") or {}
    cnt = o.get("counts") or {}
    boxes = []
    for b, (name, col, sub) in COLS.items():
        rows = (picks.get(b) or [])[:n]
        li = "".join(f'<li>{esc(p["symbol"])}<span>{esc(_v(p))}</span></li>' for p in rows) or '<li><span>none today</span></li>'
        boxes.append(f'<div style="border-color:{col}"><b style="color:{col}">{esc(name)}</b> '
                     f'<span>({cnt.get(b, 0)})</span><small>{esc(sub)}</small><ul>{li}</ul></div>')
    return (f'<div class="cx">{"".join(boxes)}</div><div class="cxl">Options data of {esc(o.get("asof"))} · rank = IV30 in '
            f'its year · pts = IV30 minus 10-day realised vol · <a href="convexity.html">full lists, ETF vol board, '
            f'track record</a></div>')
