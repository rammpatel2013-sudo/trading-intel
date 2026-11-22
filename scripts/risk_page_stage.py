"""risk_page_stage.py - created 2026-11-22. Stage-analysis cards for the Risk page (split out of risk_page_html.py
to keep modules under 400 lines): the stage mix (stacked area), today's breakouts / breakdowns, the sector rotation
graph and sectors by stage. Reads stagetab.json fields (scheduler/jobs/stage_breadth.py). Static SVG + HTML only.
"""
from __future__ import annotations

from risk_page_html import AX, GRN, INK, MON, RED, esc, num
STAGE_COL = {"s1": "#E2B53B", "s2": "#3C9D57", "s3": "#E07B33", "s4": "#C4433A"}
STAGE_TXT = {"s1": "1 Basing", "s2": "2 Rising", "s3": "3 Topping", "s4": "4 Falling"}
QUAD_COL = {"Leading": "#E3F1E8", "Weakening": "#FBF0D9", "Lagging": "#F6E1DE", "Improving": "#E3EAF7"}
QUAD_INK = {"Leading": "#2F7D4F", "Weakening": "#9A6A12", "Lagging": "#B23A2E", "Improving": "#3D63C9"}


def mix_chart(m: dict, W: int, H: int, cls: str, days: int, smooth: int = 5) -> str:
    d = (m.get("dates") or [])[-days:]
    ser = {}
    for k in ("s2", "s1", "s3", "s4"):        # 5-day average for the daily picture: single-day stage labels flicker
        raw = [v if v is not None else 0.0 for v in (m.get(k) or [])]
        sm = [sum(raw[max(0, i - smooth + 1):i + 1]) / len(raw[max(0, i - smooth + 1):i + 1]) for i in range(len(raw))]
        ser[k] = sm[-days:]
    n = len(d)
    if n < 10:
        return ""
    PL, PR, PT, PB = 34, 8, 8, 22
    iw, ih = W - PL - PR, H - PT - PB
    X = lambda i: PL + iw * i / (n - 1)  # noqa: E731
    Y = lambda v: PT + ih * (1 - v / 100)  # noqa: E731
    o = [f'<svg class="{cls}" viewBox="0 0 {W} {H}" role="img" aria-label="stage mix over time">',
         f'<rect x="{PL}" y="{PT}" width="{iw}" height="{ih}" fill="#E8EAED"/>']
    base = [0.0] * n
    for k in ("s2", "s1", "s3", "s4"):        # rising at the bottom, falling on top
        top = [base[i] + (ser[k][i] or 0) for i in range(n)]
        pts = [f"{X(i):.1f},{Y(top[i]):.1f}" for i in range(n)] + [f"{X(i):.1f},{Y(base[i]):.1f}" for i in range(n - 1, -1, -1)]
        o.append(f'<polygon points="{" ".join(pts)}" fill="{STAGE_COL[k]}" opacity="0.9"/>')
        base = top
    for v in (25, 50, 75):
        o.append(f'<line x1="{PL}" x2="{W - PR}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="#fff" stroke-opacity=".6" stroke-dasharray="3 3"/>')
        o.append(f'<text x="{PL - 5}" y="{Y(v) + 4:.1f}" text-anchor="end">{v}%</text>')
    seen = set()
    for i, s in enumerate(d):
        if i > 3 and int(s[5:7]) in ((1, 4, 7, 10) if days <= 300 else (1, 7)) and s[:7] not in seen and d[i - 1][:7] != s[:7]:
            seen.add(s[:7])
            lab = MON[int(s[5:7]) - 1] + ("" if days <= 300 else " " + s[2:4])
            o.append(f'<text x="{X(i):.1f}" y="{H - 5}" text-anchor="middle">{lab}</text>')
    o.append("</svg>")
    return "".join(o)


def _r20(r: dict) -> str:
    return "" if r.get("ret20") is None else f"{r['ret20']:+.1f}% in 20 days"


def special_body(c: dict) -> str:
    st = c.get("stage") or {}
    kid = c["id"]
    if kid == "tdex":
        from risk_page_tdex import tdex_body  # same folder (scripts/)

        return tdex_body(c.get("tdex") or {})
    if kid == "convex":
        from risk_page_convex import convex_body  # same folder (scripts/)

        return convex_body(c.get("convex") or {})
    if kid in ("width", "where"):
        from risk_page_width import where_body, width_body  # same folder (scripts/)

        return width_body(c.get("width") or {}) if kid == "width" else where_body(st)
    if kid == "mix":
        m = st.get("mix") or {}
        now = {k: (m.get(k) or [None])[-1] for k in ("s1", "s2", "s3", "s4")}
        chips = "".join(f'<span class="chip" style="background:{STAGE_COL[k]}">{STAGE_TXT[k]}: {num(now[k], 0)}%</span>'
                        for k in ("s2", "s1", "s3", "s4") if now[k] is not None)
        return (f'<div class="chips">{chips}</div>{mix_chart(m, 420, 190, "sm", 252)}{mix_chart(m, 1150, 340, "lg", 756)}'
                f'<div class="key">{esc(st.get("n_members", ""))} S&amp;P 1500 stocks · 5-day average · grey = no clear stage</div>')
    if kid == "moves":
        cnt = st.get("counts") or {}

        def lst(rows, col, word):
            if not rows:
                return f'<p class="means">No stocks {word} today.</p>'
            li = "".join(f'<li class="mv{" more" if i >= 8 else ""}"><b>{esc(r["symbol"])}</b> <span>{esc(r.get("sector") or "")}</span>'
                         f'<i style="color:{col}">{_r20(r)}</i></li>'
                         for i, r in enumerate(rows))
            return f'<ul class="mvl">{li}</ul>'
        return (f'<div class="mvh" style="color:{GRN}">&#9650; Broke out into a rising stage: {cnt.get("breakouts", 0)} stocks</div>'
                f'{lst(st.get("breakouts") or [], GRN, "broke out")}'
                f'<div class="mvh" style="color:{RED}">&#9660; Broke down into a falling stage: {cnt.get("breakdowns", 0)} stocks</div>'
                f'{lst(st.get("breakdowns") or [], RED, "broke down")}<div class="key">Biggest companies first. Click to see all.</div>')
    if kid == "wmix":
        w = st.get("weekly") or {}
        m = w.get("mix") or {}
        now = {k: (m.get(k) or [None])[-1] for k in ("s1", "s2", "s3", "s4")}
        chips = "".join(f'<span class="chip" style="background:{STAGE_COL[k]}">{STAGE_TXT[k]}: {num(now[k], 0)}%</span>'
                        for k in ("s2", "s1", "s3", "s4") if now[k] is not None)
        return (f'<div class="chips">{chips}</div>{mix_chart(m, 420, 190, "sm", 104, 1)}{mix_chart(m, 1150, 340, "lg", 156, 1)}'
                f'<div class="key">Weekly closes, 30-week average · week of {esc(w.get("asof_week") or "")} (this week so far)</div>')
    if kid == "wmoves":
        w = st.get("weekly") or {}
        cnt = w.get("counts") or {}

        def wl(rows, col, word):
            if not rows:
                return f'<p class="means">No stocks {word} this week.</p>'
            li = "".join(f'<li class="mv{" more" if i >= 8 else ""}"><b>{esc(r["symbol"])}</b> <span>{esc(r.get("sector") or "")}</span>'
                         + ("" if r.get("rs") is None else f'<i style="color:{GRN if r["rs"] > 0 else RED}">'
                            f'{"beating" if r["rs"] > 0 else "lagging"} the market</i>') + "</li>"
                         for i, r in enumerate(rows))
            return f'<ul class="mvl">{li}</ul>'
        return (f'<div class="mvh" style="color:{GRN}">&#9650; Started a long-term uptrend and beating the market: '
                f'{cnt.get("new2_strong", 0)} stocks</div>{wl(w.get("new2") or [], GRN, "started a strong long-term uptrend")}'
                f'<div class="mvh" style="color:{RED}">&#9660; Started a long-term downtrend: {cnt.get("new4", 0)} stocks</div>'
                f'{wl(w.get("new4") or [], RED, "started a long-term downtrend")}<div class="key">Biggest companies first. Click to see all.</div>')
    if kid == "industry":
        rows = st.get("industries") or []
        if not rows:
            return ""

        def row(r, more):
            bars = (f'<i style="width:{r["s2"]:.0f}%;background:{STAGE_COL["s2"]}"></i>'
                    f'<i style="width:{max(0.0, 100 - r["s2"] - r["s4"]):.0f}%;background:#D5D8DD"></i>'
                    f'<i style="width:{r["s4"]:.0f}%;background:{STAGE_COL["s4"]}"></i>')
            d = None if r.get("net_20d_ago") is None else r["net"] - r["net_20d_ago"]
            arr = '<span style="visibility:hidden">&#9650;</span>' if d is None or abs(d) < 10 else (f'<span style="color:{GRN if d > 0 else RED}">'
                                                       f'{"&#9650;" if d > 0 else "&#9660;"}</span>')
            return (f'<div class="sr ir{" more" if more else ""}"><span class="sn" title="{esc(r.get("sector") or "")}">'
                    f'{esc(r["name"])}</span><span class="sb">{bars}</span>'
                    f'<span class="sv"><b style="color:{GRN}">{r["s2"]:.0f}%</b> · <b style="color:{RED}">{r["s4"]:.0f}%</b> {arr}</span></div>')
        top, bot = rows[:6], rows[-6:] if len(rows) > 12 else []
        mid = rows[6:-6] if len(rows) > 12 else rows[6:]
        out = ('<div class="mvh" style="color:#2F7D4F">Strongest groups</div>' + "".join(row(r, False) for r in top)
               + "".join(row(r, True) for r in mid)
               + ('<div class="mvh" style="color:#B23A2E">Weakest groups</div>' + "".join(row(r, False) for r in bot) if bot else ""))
        return (f'<div class="secs">{out}</div><div class="key ik">{len(rows)} industry groups with 4+ stocks. '
                f'<b style="color:{GRN}">Green %</b> = share rising, <b style="color:{RED}">red %</b> = share falling. '
                f'Arrow = big change in the last month. Click to see every group.</div>')
    if kid == "rot":
        rows = st.get("rotation") or []
        return rot_chart(rows, 420, 330, "sm", False) + rot_chart(rows, 1000, 620, "lg", True) + rot_list(rows)
    if kid == "secstage":
        out = []
        for r in st.get("sectors") or []:
            bars = "".join(f'<i style="width:{r[k]:.0f}%;background:{STAGE_COL[k]}"></i>' for k in ("s2", "s1", "s3", "s4"))
            d = None if r.get("s2_20d_ago") is None else r["s2"] - r["s2_20d_ago"]
            arr = "" if d is None else (f'<span style="color:{GRN if d > 0 else RED}">{"&#9650;" if d > 0 else "&#9660;"}'
                                        f'{abs(d):.0f}</span>' if abs(d) >= 1 else '<span style="color:#4A4F57">&#8596;</span>')
            out.append(f'<div class="sr"><span class="sn">{esc(r["name"])}</span><span class="sb">{bars}</span>'
                       f'<span class="sv"><b>{r["s2"]:.0f}%</b> {arr}</span></div>')
        keys = "".join(f'<span><i style="background:{STAGE_COL[k]}"></i>{STAGE_TXT[k]}</span>' for k in ("s2", "s1", "s3", "s4"))
        return f'<div class="secs">{"".join(out)}</div><div class="key sk">{keys}</div>'
    return ""


def rot_chart(rows: list, W: int, H: int, cls: str, big: bool) -> str:
    pts = [(p[1], p[2]) for r in rows for p in r.get("tail") or []]
    if not pts:
        return ""
    span_x = max(abs(x - 100) for x, _ in pts) * 1.15 + 0.3
    span_y = max(abs(y - 100) for _, y in pts) * 1.15 + 0.3
    P = 10
    X = lambda v: P + (W - 2 * P) * (v - (100 - span_x)) / (2 * span_x)  # noqa: E731
    Y = lambda v: P + (H - 2 * P) * ((100 + span_y) - v) / (2 * span_y)  # noqa: E731
    cx, cy = X(100), Y(100)
    o = [f'<svg class="{cls}" viewBox="0 0 {W} {H}" role="img" aria-label="sector rotation graph">',
         f'<rect x="{cx:.1f}" y="{P}" width="{W - P - cx:.1f}" height="{cy - P:.1f}" fill="{QUAD_COL["Leading"]}"/>',
         f'<rect x="{cx:.1f}" y="{cy:.1f}" width="{W - P - cx:.1f}" height="{H - P - cy:.1f}" fill="{QUAD_COL["Weakening"]}"/>',
         f'<rect x="{P}" y="{cy:.1f}" width="{cx - P:.1f}" height="{H - P - cy:.1f}" fill="{QUAD_COL["Lagging"]}"/>',
         f'<rect x="{P}" y="{P}" width="{cx - P:.1f}" height="{cy - P:.1f}" fill="{QUAD_COL["Improving"]}"/>']
    fs = 15 if big else 13
    for q, (tx, ty, anc) in {"Leading": (W - P - 6, P + fs + 2, "end"), "Weakening": (W - P - 6, H - P - 6, "end"),
                             "Lagging": (P + 6, H - P - 6, "start"), "Improving": (P + 6, P + fs + 2, "start")}.items():
        o.append(f'<text x="{tx}" y="{ty}" text-anchor="{anc}" style="fill:{QUAD_INK[q]};font-weight:700;font-size:{fs}px">{q}</text>')
    for r in rows:
        t = r.get("tail") or []
        col = QUAD_INK.get(r["quadrant"], AX)
        if len(t) > 1:
            o.append(f'<polyline points="{" ".join(f"{X(p[1]):.1f},{Y(p[2]):.1f}" for p in t)}" fill="none" stroke="{col}" '
                     f'stroke-width="{2 if big else 1.5}" stroke-opacity=".55"/>')
            for p in t[:-1]:
                o.append(f'<circle cx="{X(p[1]):.1f}" cy="{Y(p[2]):.1f}" r="{2.2 if big else 1.6}" fill="{col}" fill-opacity=".5"/>')
        x, y = X(t[-1][1]), Y(t[-1][2])
        o.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{6 if big else 4.5}" fill="{col}"/>')
        o.append(f'<text x="{x + 7:.1f}" y="{y - 6:.1f}" style="font-weight:700;fill:{INK};font-size:{fs}px;'
                 f'paint-order:stroke;stroke:#fff;stroke-width:3px">{esc(r["symbol"])}</text>')
    o.append("</svg>")
    return "".join(o)


def rot_list(rows: list) -> str:
    order = {"Leading": 0, "Improving": 1, "Weakening": 2, "Lagging": 3}
    li = "".join(f'<li><span class="q" style="background:{QUAD_COL[r["quadrant"]]};color:{QUAD_INK[r["quadrant"]]}">{r["quadrant"]}</span>'
                 f'<span><b>{esc(r["name"])}</b> {esc(r["symbol"])}</span>'
                 + ("" if r.get("vs_spy20") is None else f'<i>{r["vs_spy20"]:+.1f}% vs S&amp;P (20d)</i>') + "</li>"
                 for r in sorted(rows, key=lambda r: (order.get(r["quadrant"], 9), -r["ratio"])))
    return f'<ul class="rl">{li}</ul>'


STAGE_CSS = """
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 6px} .chip{color:#fff;font:700 14px system-ui,sans-serif;padding:3px 9px;border-radius:12px}
.mvh{font:700 16px system-ui,sans-serif;margin:10px 0 4px} .mvl{list-style:none;margin:0;padding:0;font-size:15px}
.mvl li{display:flex;gap:8px;align-items:baseline;padding:3px 0;border-bottom:1px solid #EEF0F3} .mvl li span{color:#4A4F57;font-size:14px;flex:1}
.mvl li i{font-style:normal;font-weight:600} .mvl li.more{display:none} .card.big .mvl li.more{display:flex}
.rl{list-style:none;margin:8px 0 0;padding:0;font-size:15px} .rl li{display:grid;grid-template-columns:92px minmax(0,1fr) auto;gap:8px;align-items:baseline;padding:3px 0}
.rl .q{font:700 13px system-ui,sans-serif;padding:1px 7px;border-radius:9px;min-width:86px;text-align:center} .rl li span{color:#4A4F57}
.rl li i{font-style:normal;color:#2E3238;white-space:nowrap}
.secs{margin-top:8px} .sr{display:grid;grid-template-columns:110px minmax(0,1fr) 120px;gap:8px;align-items:center;margin:5px 0;font-size:15px}
.sb{display:flex;height:16px;border-radius:4px;overflow:hidden;background:#E8EAED} .sb i{display:block;height:100%}
.sv{font-size:14px;text-align:right} .sk{gap:10px} .sk span{display:inline-flex;align-items:center;gap:4px} .sk i{display:inline-block;width:12px;height:12px;border-radius:2px}
.ir{grid-template-columns:minmax(0,190px) minmax(0,1fr) 104px} .ir .sv{white-space:nowrap} .ik{display:block} .ir .sn{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ir.more{display:none} .card.big .ir.more{display:grid}
@media(max-width:430px){.sr{grid-template-columns:86px minmax(0,1fr) 96px;font-size:14px} .ir{grid-template-columns:minmax(0,1fr) 104px;row-gap:2px} .ir .sn{grid-column:1/-1;white-space:normal}}
"""
