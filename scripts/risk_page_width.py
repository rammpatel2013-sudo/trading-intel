"""risk_page_width.py - created 2026-11-22. Two Risk-page cards in the "together" group:
  width  "Is this fear broad or narrow?"  COR1M vs VIX scatter (every close since 2006), today vs past days with
         a similar VIX, past first VIX-30 days and what SPX did next, the last year's correlation gap.
  where  "Where the stress is - and where it is not"  sector table (20-day move, share of stocks in a falling
         stage, share 20%+ below their 52-week high) + the weakest / strongest industry groups.
Data: fusetab.json "width" (scripts/fear_width.py) and stagetab.json (sectors / rotation / industries).
Static SVG + HTML only.
"""
from __future__ import annotations

from risk_page_html import AX, GRN, INK, MON, RED, esc, num

READ_COL = {"Narrow": "#B5651D", "Broad": RED, "Normal mix": "#4A4F57"}
READ_TXT = {
    "Narrow": "The index is scared, but stocks are NOT falling together. The stress sits in a few places.",
    "Broad": "Stocks are moving as one. Trouble is everywhere, not in a few corners.",
    "Normal mix": "Stocks are moving together about as much as usual for this level of fear.",
}


def _scatter(w: dict, W: int, H: int, cls: str, big: bool) -> str:
    pts = w.get("points") or []
    if not pts:
        return ""
    xmax = max(50.0, min(85.0, max(p[0] for p in pts) + 2))
    PL, PR, PT, PB = 36, 10, 10, 26
    iw, ih = W - PL - PR, H - PT - PB
    X = lambda v: PL + iw * (min(v, xmax) - 9) / (xmax - 9)  # noqa: E731
    Y = lambda c: PT + ih * (1 - max(0.0, min(c, 100.0)) / 100)  # noqa: E731
    o = [f'<svg class="{cls}" viewBox="0 0 {W} {H}" role="img" aria-label="implied correlation against VIX">',
         f'<rect x="{PL}" y="{PT}" width="{iw}" height="{ih}" fill="#FAFBFC" stroke="#E3E6EA"/>']
    for v in (20, 40, 60, 80):
        o.append(f'<line x1="{PL}" x2="{W - PR}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="#E8EAED"/>'
                 f'<text x="{PL - 5}" y="{Y(v) + 4:.1f}" text-anchor="end">{v}</text>')
    for v in (10, 20, 30, 40, 50, 60, 70, 80):
        if v <= xmax:
            o.append(f'<text x="{X(v):.1f}" y="{H - 8}" text-anchor="middle">{v}</text>')
    # normal range at today's VIX (q25-q75 of days with a similar VIX)
    if w.get("near_q25") is not None:
        x0, x1 = X(w["vix"] - 3), X(w["vix"] + 3)
        o.append(f'<rect x="{x0:.1f}" y="{Y(w["near_q75"]):.1f}" width="{x1 - x0:.1f}" '
                 f'height="{Y(w["near_q25"]) - Y(w["near_q75"]):.1f}" fill="#3D63C9" fill-opacity=".12" '
                 f'stroke="#3D63C9" stroke-opacity=".35" stroke-dasharray="3 3"/>')
    r = 1.6 if big else 1.2
    o.append("".join(f'<circle cx="{X(p[0]):.1f}" cy="{Y(p[1]):.1f}" r="{r}" fill="#B9BEC6" fill-opacity=".55"/>'
                     for p in pts))
    fs = 13 if big else 11
    placed: list = []
    past = sorted([t for t in w.get("touches") or [] if t["date"] != w.get("asof")], key=lambda t: -t["cor1m"])
    for t in past:
        cx, cy = X(t["vix"]), Y(t["cor1m"])
        o.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{4.5 if big else 3.5}" fill="{INK}"/>')
        if not big and t["date"] < "2024":
            continue                                   # small card: label only the recent ones
        lw = 7.2 * fs / 13 * 7
        for lx, ly, anc in ((cx + 6, cy - 5, "start"), (cx - 6, cy - 5, "end"), (cx + 6, cy + fs + 2, "start"),
                            (cx - 6, cy + fs + 2, "end")):
            x0 = lx if anc == "start" else lx - lw
            if all(abs(ly - py) > fs or x0 + lw < px0 or x0 > px0 + lw for px0, py in placed):
                placed.append((x0, ly))
                o.append(f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anc}" style="font-size:{fs}px;fill:{INK};'
                         f'paint-order:stroke;stroke:#fff;stroke-width:3px">{esc(t["date"][:7])}</text>')
                break
    cx, cy = X(w["vix"]), Y(w["cor1m"])
    o.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{8 if big else 6}" fill="{RED}" stroke="#fff" stroke-width="2"/>'
             f'<text x="{cx + 10:.1f}" y="{cy + 16:.1f}" style="font-weight:700;font-size:{fs + 1}px;fill:{RED};'
             f'paint-order:stroke;stroke:#fff;stroke-width:3px">now</text>')
    o.append(f'<text x="{W - PR}" y="{PT + 14}" text-anchor="end" style="font-size:{fs}px;fill:{AX}">'
             f'up = stocks move as one</text></svg>')
    return "".join(o)


def _gap(w: dict, W: int, H: int, cls: str) -> str:
    g = w.get("gap") or []
    d = w.get("gap_dates") or []
    ok = [(i, v) for i, v in enumerate(g) if v is not None]
    if len(ok) < 20:
        return ""
    lo, hi = min(-15.0, min(v for _, v in ok) - 3), max(10.0, max(v for _, v in ok) + 3)
    PL, PR, PT, PB = 36, 8, 6, 20
    iw, ih = W - PL - PR, H - PT - PB
    X = lambda i: PL + iw * i / max(1, len(g) - 1)  # noqa: E731
    Y = lambda v: PT + ih * (hi - v) / (hi - lo)  # noqa: E731
    o = [f'<svg class="{cls}" viewBox="0 0 {W} {H}" role="img" aria-label="correlation gap over the last year">',
         f'<rect x="{PL}" y="{Y(-10):.1f}" width="{iw}" height="{Y(lo) - Y(-10):.1f}" fill="#FBF0D9" fill-opacity=".6"/>',
         f'<line x1="{PL}" x2="{W - PR}" y1="{Y(0):.1f}" y2="{Y(0):.1f}" stroke="#9AA0A8"/>',
         f'<text x="{PL - 5}" y="{Y(0) + 4:.1f}" text-anchor="end">0</text>',
         f'<polyline fill="none" stroke="{INK}" stroke-width="1.6" points="'
         + " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in ok) + '"/>']
    seen = set()
    for i, s in enumerate(d):
        if i > 3 and int(s[5:7]) in (1, 4, 7, 10) and s[:7] not in seen and d[i - 1][:7] != s[:7]:
            seen.add(s[:7])
            o.append(f'<text x="{X(i):.1f}" y="{H - 5}" text-anchor="middle">{MON[int(s[5:7]) - 1]}</text>')
    o.append(f'<text x="{PL + 4}" y="{PT + 12}" style="fill:#9A6A12">below 0 = narrower than usual for the VIX (vs last 4 years)</text></svg>')
    return "".join(o)


def width_body(w: dict) -> str:
    if not w or w.get("vix") is None:
        return ""
    col = READ_COL.get(w["read"], INK)
    yr = (w.get("era_from") or "")[:4]
    pa = w.get("pct_all")
    head = (f'<div class="wv" style="color:{col}">{esc(w["read"])}</div>'
            f'<p class="wl">VIX <b>{num(w["vix"], 1)}</b>; options price stocks to move together <b>{num(w["cor1m"], 0)}</b>. '
            f'Since {esc(yr)}, days with a VIX like this had {num(w["near_q25"], 0)}–{num(w["near_q75"], 0)} '
            f'(today is higher than {w["pct"]}% of them).</p>')
    if pa is not None:
        head += (f'<p class="wl wsm">Against every day since 2006 it is narrower than {100 - pa}% — but stocks have moved '
                 f'together less and less since about 2017, and every big VIX jump since 2024 looked like this.</p>')
    head += f'<p class="means" style="margin-top:2px">{esc(READ_TXT.get(w["read"], ""))}</p>'
    rows = []
    past = [t for t in (w.get("touches") or []) if t["date"] != w.get("asof")]
    for t in reversed(past):
        c = lambda v: "" if v is None else f'<td style="color:{GRN if v >= 0 else RED}">{v:+.1f}%</td>'  # noqa: E731
        rows.append(f'<tr class="{"" if t["read"] == "Narrow" else "more"}"><td>{esc(t["date"][:7])}</td>'
                    f'<td>{num(t["vix"], 0)}</td><td style="color:{READ_COL.get(t["read"], INK)}">{esc(t["read"])}</td>'
                    f'{c(t["r20"]) or "<td></td>"}{c(t["r60"]) or "<td></td>"}{c(t["worst20"]) or "<td></td>"}</tr>')
    nar = [t for t in past if t["read"] == "Narrow" and t["r20"] is not None]
    up = sum(1 for t in nar if t["r20"] > 0)
    tbl = (f'<details class="hist"><summary>History: {len(past)} past first days with VIX 30+ ' +
           (f'<span class="wsub">· narrow ones: S&amp;P up 20 days later {up} of {len(nar)}</span>' if nar else "") +
           '</summary><table class="wt"><tr><th>When</th><th>VIX</th><th>Fear was</th><th>S&amp;P 20d</th>'
           '<th>60d</th><th>Worst in 20d</th></tr>' + "".join(rows) + '</table>'
           '<div class="key">Only a handful of cases (2008 was narrow too and kept falling) — a guide, not a rule. '
           '</div></details>')
    return (head + _scatter(w, 420, 270, "sm", False) + _scatter(w, 1000, 520, "lg", True)
            + '<div class="mvh">Last year: correlation vs normal for the VIX level</div>'
            + _gap(w, 420, 120, "sm") + _gap(w, 1000, 200, "lg") + tbl)


def _cell(v, worse_high: bool, lo: float, hi: float, fmt: str) -> str:
    if v is None:
        return "<td></td>"
    f = max(0.0, min(1.0, (v - lo) / (hi - lo)))
    f = f if worse_high else 1 - f
    bg = f"rgba(196,67,58,{0.08 + 0.55 * f:.2f})" if f > 0.5 else f"rgba(60,157,87,{0.08 + 0.45 * (1 - f):.2f})"
    return f'<td style="background:{bg}">{fmt.format(v)}</td>'


def where_body(st: dict) -> str:
    secs = st.get("sectors") or []
    if not secs:
        return ""
    rot = {r["symbol"]: r for r in st.get("rotation") or []}
    rows = []
    for s in secs:
        vs = (rot.get(s["etf"]) or {}).get("vs_spy20")
        score = (s.get("s4") or 0) + (s.get("dd20") or 0) - (s.get("s2") or 0) - (vs or 0)
        rows.append({**s, "vs": vs, "score": score})
    rows.sort(key=lambda r: -r["score"])
    k = max(1, len(rows) // 3)
    hot = [r["name"] for r in rows[:k]]
    calm = [r["name"] for r in rows[-k:]][::-1]
    out = [f'<p class="wl"><b style="color:{RED}">Stress is here:</b> {esc(", ".join(hot))}.<br>'
           f'<b style="color:{GRN}">Holding up:</b> {esc(", ".join(calm))}.</p>',
           '<table class="wt wh"><tr><th>Sector</th><th>vs S&amp;P<br>20d</th><th>Stocks<br>falling</th>'
           '<th>Down 20%+<br>from high</th></tr>']
    for i, r in enumerate(rows):
        sep = ' class="cut"' if i == k or i == len(rows) - k else ""
        out.append(f'<tr{sep}><td>{esc(r["name"])} <span class="tk">{esc(r["etf"])}</span></td>'
                   + _cell(r["vs"], False, -8, 8, "{:+.1f}%") + _cell(r.get("s4"), True, 0, 60, "{:.0f}%")
                   + _cell(r.get("dd20"), True, 0, 50, "{:.0f}%") + "</tr>")
    out.append("</table>")
    ind = st.get("industries") or []
    if len(ind) >= 6:
        weak = [i for i in ind[::-1] if i["net"] < 0][:6]
        strong = [i for i in ind if i["net"] > 0][:6]
        chip = lambda i, c: f'<span class="ic" style="border-color:{c}">{esc(i["name"])} <b style="color:{c}">{i["s4"] if c == RED else i["s2"]:.0f}%</b></span>'  # noqa: E731
        out.append('<div class="mvh">Hardest-hit industry groups <span class="wsub">(% of stocks falling)</span></div>'
                   + "".join(chip(i, RED) for i in weak))
        if strong:
            out.append('<div class="mvh">Groups still rising <span class="wsub">(% of stocks rising)</span></div>'
                       + "".join(chip(i, GRN) for i in strong))
    out.append('<div class="key">Sorted most stressed first. Red cells = worse than usual, green = holding up. '
               '"vs S&P" = the sector fund (big companies weigh most); "Falling" = Stage 4; "Down 20%+" = share of the sector\'s stocks 20% or more below their 52-week '
               'high.</div>')
    return "".join(out)


WIDTH_CSS = """
.wv{font:800 26px system-ui,sans-serif;margin:6px 0 2px} .wl{font-size:15px;line-height:1.45;margin:4px 0}
.wsm{font-size:13px;color:#4A4F57} .wsub{font-weight:400;color:#4A4F57;font-size:13px}
.wt{border-collapse:collapse;width:100%;font-size:14px;margin:4px 0} .wt th{font:600 12px system-ui,sans-serif;color:#4A4F57;text-align:right;padding:3px 5px;border-bottom:1px solid #E3E6EA}
.wt th:first-child,.wt td:first-child{text-align:left} .wt td{padding:4px 5px;text-align:right;border-bottom:1px solid #F0F2F4}
.wt tr.more{display:none} .card.big .wt tr.more{display:table-row} .wh tr.cut td{border-top:2px solid #C9CDD3}
.tk{color:#8A9099;font-size:12px} .ic{display:inline-block;border:1px solid;border-radius:12px;padding:2px 8px;margin:3px 4px 0 0;font-size:13px}
"""
