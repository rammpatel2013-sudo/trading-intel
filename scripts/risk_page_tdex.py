"""risk_page_tdex.py - created 2026-11-22. Risk-page card "Tail insurance: fear level or extra demand?" ("fear" group).
Data: fusetab.json "tdex" (scripts/tdex_read.py). Static SVG + HTML only; reuses WIDTH_CSS classes.
"""
from __future__ import annotations

from risk_page_html import AMB, GRN, INK, MON, RED, esc, num

READ_COL = {"Pre-hedge": AMB, "Flush": RED, "Elevated": INK, "Normal": "#4A4F57"}
READ_TXT = {
    "Pre-hedge": "Tail puts are expensive while the S&P 500 is still near its high. In the past this was "
                 "protection bought BEFORE the drop: more downside usually came first.",
    "Flush": "Tail puts are expensive after a real drop, with fear piled into this week. In the past this was the "
             "capitulation kind: the low usually came within days (not always: 2015 retested months later).",
    "Elevated": "Tail puts are expensive, but the mix does not match either past pattern cleanly.",
    "Normal": "Tail insurance is not unusually expensive.",
}


def _line(x: dict, key: str, ref: float, W: int, H: int, cls: str, lab: str) -> str:
    s = x.get(key) or []
    d = x.get("dates") or []
    if len(s) < 20:
        return ""
    lo, hi = min(min(s), ref) * 0.9, max(max(s), ref) * 1.08
    PL, PR, PT, PB = 36, 8, 8, 20
    iw, ih = W - PL - PR, H - PT - PB
    X = lambda i: PL + iw * i / max(1, len(s) - 1)  # noqa: E731
    Y = lambda v: PT + ih * (hi - v) / (hi - lo)  # noqa: E731
    o = [f'<svg class="{cls}" viewBox="0 0 {W} {H}" role="img" aria-label="{esc(lab)}">',
         f'<rect x="{PL}" y="{PT}" width="{iw}" height="{max(0.0, Y(ref) - PT):.1f}" fill="#FBF0D9" fill-opacity=".6"/>',
         f'<line x1="{PL}" x2="{W - PR}" y1="{Y(ref):.1f}" y2="{Y(ref):.1f}" stroke="{RED}" stroke-dasharray="6 4"/>',
         f'<text x="{PL - 5}" y="{Y(ref) + 4:.1f}" text-anchor="end">{ref:g}</text>',
         f'<polyline fill="none" stroke="{INK}" stroke-width="1.6" points="'
         + " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(s)) + '"/>',
         f'<circle cx="{X(len(s) - 1):.1f}" cy="{Y(s[-1]):.1f}" r="4" fill="{RED}"/>']
    seen = set()
    for i, t in enumerate(d):
        if i > 3 and int(t[5:7]) in (1, 4, 7, 10) and t[:7] not in seen and d[i - 1][:7] != t[:7]:
            seen.add(t[:7])
            o.append(f'<text x="{X(i):.1f}" y="{H - 5}" text-anchor="middle">{MON[int(t[5:7]) - 1]}</text>')
    o.append(f'<text x="{PL + 4}" y="{PT + 12}" style="fill:#9A6A12">{esc(lab)}</text></svg>')
    return "".join(o)


def tdex_body(x: dict) -> str:
    if not x or x.get("tdex") is None:
        return ""
    col = READ_COL.get(x["read"], INK)
    extra = "more than" if x["tv"] >= 1.1 else "about what"
    head = (f'<div class="wv" style="color:{col}">{esc(x["read"])}</div>'
            f'<p class="wl">TailDex <b>{num(x["tdex"], 1)}</b>, higher than {x["pct"]}% of closes since {esc(x["start"][:4])}. '
            f'TailDex ÷ VIX <b>{num(x["tv"], 2)}</b> (usual {num(x["tv_med"], 2)}): tail puts cost {extra} a VIX of '
            f'{num(x["vix"], 0)} implies. S&amp;P 500 {num(x["dd"], 1)}% from its 1-year high'
            + (f'; this week\'s fear minus 3-month fear {x["term"]:+.1f}' if x.get("term") is not None else "") + '.</p>'
            f'<p class="means" style="margin-top:2px">{esc(READ_TXT.get(x["read"], ""))}</p>')
    eps = [e for e in x.get("episodes") or [] if e["date"] != x.get("asof")]
    rows = []
    for e in reversed(eps):
        c = lambda v: f'<td style="color:{GRN if v >= -0.05 else RED}">{v:+.1f}%</td>'  # noqa: E731
        show = e["read"] in ("Pre-hedge", "Flush")
        rows.append(f'<tr class="{"" if show else "more"}"><td>{esc(e["date"][:7])}</td>'
                    f'<td style="color:{READ_COL.get(e["read"], INK)}">{esc(e["read"])}</td><td>{num(e["tdex"], 0)}</td>'
                    f'<td>{num(e["vix"], 0)}</td><td>{e["dd"]:+.0f}%</td>{c(e["more3m"])}<td>{e["days_low"]}</td></tr>')
    fl = [e for e in eps if e["read"] == "Flush"]
    ph = [e for e in eps if e["read"] == "Pre-hedge"]
    sub = []
    if fl:
        sub.append(f'flush: low within a week {sum(1 for e in fl if e["days_low"] <= 7)} of {len(fl)}')
    if ph:
        sub.append(f'pre-hedge: average further drop {sum(e["more3m"] for e in ph) / len(ph):.1f}%')
    tbl = (f'<details class="hist"><summary>History: {len(eps)} past times TailDex closed a week at 25+ '
           + (f'<span class="wsub">· {esc("; ".join(sub))}</span>' if sub else "")
           + '</summary><table class="wt"><tr><th>When</th><th>Type</th><th>TDEX</th><th>VIX</th><th>S&amp;P off high</th>'
           '<th>More drop<br>in 3 mo</th><th>Days to<br>6-mo low</th></tr>' + "".join(rows) + '</table>'
           '<div class="key">TailDex = price of 30-day puts 3 standard moves below SPY. Closing values only: its intraday '
           f'highs carry opening misprints. {len(fl) + len(ph)} Flush/Pre-hedge cases: a guide, not a rule. 2022-04 fell 11% '
           'more after a similar drop with a flat fear curve.</div></details>')
    return (head + _line(x, "s_tdex", 25, 420, 140, "sm", "TailDex, last year (25 = rare)")
            + _line(x, "s_tdex", 25, 1000, 230, "lg", "TailDex, last year (25 = rare)")
            + _line(x, "s_tv", 1.1, 1000, 170, "lg", "TailDex ÷ VIX (above 1.1 = tail puts bid beyond the VIX level)")
            + tbl)
