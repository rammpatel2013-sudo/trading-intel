"""
simple_card_html.py - created 2026-11-14. Writes the plain-language scorecard page (simple_card.py) as finished HTML.

Why (2026-11-14): the 11/13 page drew every number, row, card and chart with page scripts. Phone file viewers
(Telegram's file preview) do not run scripts, so on a phone only the headings and empty boxes showed. Now Python
writes everything: numbers and rows as HTML, both charts as inline SVG. The one small script left only adds the
tap-for-numbers cards in a browser; the page is complete without it.
"""
import html
import math

BOOK_LABEL = "of $50,000"
GLOSSARY = [
    ("The map", "The cockpit's picture of where big option dealers have to buy or sell as SPX moves."),
    ("Balance line", "The map's middle line. Above it, dealers' hedging tends to calm price; below it, it tends to speed moves up."),
    ("Top line / bottom line", "The map's outer lines, where price often stalls."),
    ("Ceiling / floor", "The lines the map expects to stop the day's high and low."),
    ("Calm day / wild day", "Calm: dealers buy dips and sell rips, so moves snap back. Wild: dealers chase the move, so it keeps going."),
    ("Morning close range", "At 9:31 the map gives a range where the close should land about 8 days in 10."),
    ("Expected move", "How far option prices say SPX will move today. Options usually expect more than really happens."),
    ("Butterfly", "A cheap option bet that pays the most if SPX closes near one price."),
    ("Simple guess", "What anyone could use without the map: expected-move levels, yesterday's high/low, a coin flip, or today's price."),
]
OK = ('<svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true"><path d="M4 10.5l4 4 8-9" fill="none" '
      'stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg>')
NO = ('<svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true"><path d="M5 5l10 10M15 5L5 15" fill="none" '
      'stroke="#fff" stroke-width="3" stroke-linecap="round"/></svg>')
DASH = ('<svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true"><path d="M5 10h10" stroke="#fff" '
        'stroke-width="3" stroke-linecap="round"/></svg>')


# ------------------------------------------------------------------ small helpers
def _e(s):
    return html.escape(str(s), quote=True)


def money(v):
    return ("+$" if v >= 0 else "−$") + format(abs(round(v)), ",")


def _axis_money(v):
    return "$0" if v == 0 else ("$" if v > 0 else "−$") + format(abs(round(v)), ",")


def _num(v):
    return format(round(v), ",")


def hhmm(m):
    h, mi = divmod(int(round(m)), 60)
    ap = "pm" if h >= 12 else "am"
    h = h - 12 if h > 12 else h
    return "%d:%02d %s" % (h, mi, ap)


def _text(x, y, s, anchor="start", cls=""):
    return '<text x="%.1f" y="%.1f" text-anchor="%s"%s>%s</text>' % (x, y, anchor, ' class="%s"' % cls if cls else "", _e(s))


def _line(x1, x2, y1, y2, cls, extra=""):
    return '<line x1="%.1f" x2="%.1f" y1="%.1f" y2="%.1f" class="%s"%s/>' % (x1, x2, y1, y2, cls, extra)


def _hit(a, b, top, h, tip, cx):
    return '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" class="hit" data-tip="%s" data-cx="%.1f"/>' % (
        a, top, max(0.5, b - a), h, _e(tip), cx)


def _tile(k, v, s=""):
    return '<div class="tile"><div class="k">%s</div><div class="v">%s</div><div class="s">%s</div></div>' % (
        _e(k), _e(v), _e(s) if s else "&nbsp;")


def _legend(items):
    return '<div class="legend">%s</div>' % "".join('<span><i style="%s"></i>%s</span>' % (st, _e(t)) for st, t in items)


# ------------------------------------------------------------------ 1 money
def _cum_svg(M):
    W, H, L, R, T, B = 400, 210, 74, 14, 14, 34
    days = M["days"]
    o = ['<svg viewBox="0 0 %d %d" role="img" aria-label="Running practice profit by day">' % (W, H)]
    if len(days) < 2:
        return o[0] + _text(W / 2, H / 2, "Chart starts after two nights", "middle") + "</svg>"
    cum, run = [], 0.0
    for r in days:
        run += r["pl"]
        cum.append(run)
    lo, hi = min(0.0, min(cum)), max(0.0, max(cum))
    pad = max(50.0, (hi - lo) * 0.12)
    y0, y1 = lo - pad, hi + pad
    n = len(days)

    def x(i):
        return L + (W - L - R) * i / (n - 1)

    def y(v):
        return T + (H - T - B) * (1 - (v - y0) / (y1 - y0))

    step = next((s for s in (100, 250, 500, 1000, 2500, 5000, 10000, 25000, 50000) if (y1 - y0) / s <= 4), 100000)
    v = math.ceil(y0 / step) * step
    while v <= y1:
        o.append(_line(L, W - R, y(v), y(v), "s-ink2" if v == 0 else "s-grid", ' stroke-width="%s"' % (1.5 if v == 0 else 1)))
        o.append(_text(L - 8, y(v) + 5, _axis_money(v), "end"))
        v += step
    col = "gain" if run >= 0 else "loss"
    pts = " ".join("L%.1f %.1f" % (x(i), y(c)) for i, c in enumerate(cum))
    o.append('<path d="M%.1f %.1f %s L%.1f %.1f Z" class="f-%s" opacity=".12"/>' % (x(0), y(0), pts, x(n - 1), y(0), col))
    o.append('<path d="M%s" class="s-%s" fill="none" stroke-width="2.5" stroke-linejoin="round"/>' % (pts[1:], col))
    for i in sorted({0, (n - 1) // 2, n - 1}):
        o.append(_text(x(i), H - 10, days[i]["d"], "start" if i == 0 else "end" if i == n - 1 else "middle"))
    o.append(_line(0, 0, T, H - B, "cross s-ink2", ' opacity="0"'))
    r = 3 if n > 40 else 5
    for i, c in enumerate(cum):
        o.append('<circle cx="%.1f" cy="%.1f" r="%d" class="f-%s s-card" stroke-width="2"/>' % (
            x(i), y(c), r, "gain" if days[i]["pl"] >= 0 else "loss"))
    half = (x(1) - x(0)) / 2
    for i, c in enumerate(cum):
        d = days[i]
        tip = "<b>%s</b><br>%s%s (%d trades)<br>Running total: %s" % (
            _e(d["d"]), "Those two days: " if d.get("seed") else "That day: ", money(d["pl"]), d["n"], money(c))
        o.append(_hit(max(L, x(i) - half), min(W - R, x(i) + half), T, H - T - B, tip, x(i)))
    return "".join(o) + "</svg>"


def _sec_money(D):
    M = D["money"]
    tot = M["total"]
    cls, ic, word = (("gain", "▲", "YES — up overall") if tot > 0 else
                     ("loss", "▼", "NO — down overall") if tot < 0 else ("wait", "–", "Flat so far"))
    if M["nights"] < D["min_nights"]:
        word += " (early days)"
    tp = M["today_pl"]
    tiles = (_tile("Today", "–" if tp is None else money(tp), "no trades file" if tp is None else "%d trades" % M["today_n"])
             + _tile("Up nights", "%d of %d" % (M["up_nights"], M["nights"]),
                     "%d%%" % round(100.0 * M["up_nights"] / M["nights"]) if M["nights"] else "")
             + _tile("Account", "–" if M["pct"] is None else "%+.1f%%" % M["pct"], BOOK_LABEL))
    leg = _legend([("width:22px;height:3px;background:var(--gain)", "Running total"),
                   ("width:10px;height:10px;border-radius:50%;background:var(--gain)", "Up day"),
                   ("width:10px;height:10px;border-radius:50%;background:var(--loss)", "Down day")])
    return ('<section class="sec1"><h2><span class="n">1</span>Is it making money?</h2>'
            '<p class="lead">Practice trades that follow the map\'s rules, every day since %s.</p>'
            '<span class="pill %s"><span class="ic">%s</span>%s</span>'
            '<div class="hero"><span class="big">%s</span><span class="what">since %s · %d trades</span></div>'
            '<div class="tiles">%s</div>%s%s'
            '<p class="note">Paper trades: 1 contract each, real bid/ask prices, no real money. Not advice.</p></section>') % (
        _e(D["since"]), cls, ic, _e(word), money(tot), _e(D["since"]), M["n"], tiles, _cum_svg(M), leg)


# ------------------------------------------------------------------ 2 trade rules
def _sec_strats(M):
    S = M["strats"]
    mx = max([1] + [abs(s["pl"]) for s in S])
    rows = []
    for s in S:
        bits = ["%d trades" % s["n"]]
        if s["win"] is not None:
            bits.append("wins %d%%" % s["win"])
        if s["avg"] is not None:
            bits.append("avg " + money(s["avg"]))
        if s["few"]:
            bits.append("few trades so far")
        up = s["pl"] >= 0
        tip = "<b>%s</b><br>%d trades%s<br>Total %s%s" % (
            _e(s["name"]), s["n"], " · wins %d%% (since Oct 28)" % s["win"] if s["win"] is not None else "",
            money(s["pl"]), "<br>Average %s per trade" % money(s["avg"]) if s["avg"] is not None else "")
        rows.append('<div class="brow" data-tip="%s"><div class="bname"><span>%s</span><span class="amt"><span class="%s">%s</span> %s</span></div>'
                    '<div class="bsub">%s</div><div class="bsub">%s</div><div class="btrack"><span class="mid"></span>'
                    '<span class="fill %s" style="width:%.1f%%"></span></div></div>' % (
                        _e(tip), _e(s["name"]), "up" if up else "dn", "▲" if up else "▼", money(s["pl"]), _e(s["sub"]),
                        _e(" · ".join(bits)), "g" if up else "l", abs(s["pl"]) / mx * 50))
    return ('<section class="sec2"><h2><span class="n">2</span>Which trades make money?</h2>'
            '<p class="lead">Seven fixed trade rules, best at the top. Green = made money, red = lost money.</p>'
            '<div>%s</div><div class="axisrow"><span>◀ lost money</span><span>made money ▶</span></div></section>') % "".join(rows)


# ------------------------------------------------------------------ 3 signals
def _bar(v, color, op, top, unit):
    if v is None:
        return '<span class="t"></span><span class="val">–</span>'
    return '<span class="t"><b style="width:%.1f%%;background:%s;opacity:%s"></b></span><span class="val">%s%s</span>' % (
        min(100.0, v / top * 100), color, op, v, unit)


def _sec_signals(D):
    mn = D["min_nights"]
    out = ['<section class="sec3"><h2><span class="n">3</span>Which map signals work?</h2>'
           '<p class="rule">Each signal is checked against a <b>simple guess</b> anyone could make. <b>Working</b> = beats the '
           'simple guess over %d+ nights. <b>Too early</b> = fewer than %d nights, or too close to call.</p>' % (mn, mn)]
    for st, ic, label in (("gain", "✓", "Working"), ("wait", "⏳", "Too early to tell"), ("loss", "✕", "Not working")):
        lst = [s for s in D["signals"] if s["st"] == st]
        if not lst:
            continue
        out.append('<div class="grp"><span class="pill %s"><span class="ic">%s</span>%s</span><span class="cnt">%d signal%s</span></div>'
                   '<div class="cards">' % (st, ic, label, len(lst), "s" if len(lst) > 1 else ""))
        for s in lst:
            pct = s["kind"] == "pct"
            unit = "%" if pct else " pts"
            vals = [v for v in (s["map"], s["base"]) if v is not None]
            top = 100.0 if pct else max([1] + vals) * 1.15
            lean = '<div class="lean">%s</div>' % _e(s["lean_text"]) if st == "wait" and s.get("lean_text") else ""
            out.append('<div class="card %s"><h3>%s</h3><p class="what">%s</p>'
                       '<div class="cmp"><span><b>Map</b></span>%s</div><div class="cmp"><span>%s</span>%s</div>%s'
                       '<div class="foot">%s</div></div>' % (
                           st, _e(s["name"]), _e(s["what"]), _bar(s["map"], "var(--c)", 1, top, unit), _e(s["baseName"]),
                           _bar(s["base"], "var(--ink2)", 0.55, top, unit), lean, _e(s["foot"])))
        out.append("</div>")
    return "".join(out) + "</section>"


# ------------------------------------------------------------------ 4 today
def _day_svg(T):
    P = T["path"]
    W, H, L, R, Tp, B = 400, 300, 56, 74, 14, 34
    ys = [p[1] for p in P] + list(T["band"] or []) + [v for v in (T["ceiling"], T["floor"]) if v is not None]
    lo, hi = min(ys) - 5, max(ys) + 5
    m0, m1 = min(570, P[0][0]), max(960, P[-1][0])

    def x(m):
        return L + (W - L - R) * (m - m0) / (m1 - m0)

    def y(v):
        return Tp + (H - Tp - B) * (1 - (v - lo) / (hi - lo))

    def p_at(m):
        best = P[0][1]
        for t, s in P:
            if t > m:
                break
            best = s
        return best

    o = ['<svg viewBox="0 0 %d %d" role="img" aria-label="SPX price today with the map\'s levels">' % (W, H)]
    if T["band"]:
        b0, b1 = T["band"]
        o.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" class="f-band" opacity=".6"/>' % (L, y(b1), W - L - R, y(b0) - y(b1)))
    step = next((s for s in (5, 10, 20, 25, 50, 100) if (hi - lo) / s <= 7), 200)
    v = math.ceil(lo / step) * step
    while v <= hi:
        o.append(_line(L, W - R, y(v), y(v), "s-grid"))
        o.append(_text(L - 6, y(v) + 5, _num(v), "end"))
        v += step
    for v, lab in ((T["ceiling"], "Ceiling"), (T["floor"], "Floor")):
        if v is not None:
            o.append(_line(L, W - R, y(v), y(v), "s-ink2", ' stroke-width="2" stroke-dasharray="7 5"'))
            o.append(_text(W - R + 6, y(v) + 5, lab, cls="lab"))
    for m in (600, 720, 840, 960):                    # 10 am, 12 pm, 2 pm, 4 pm: short labels that fit a phone
        if m0 <= m <= m1:
            o.append(_text(x(m), H - 10, hhmm(m).replace(":00", ""), "end" if m == m1 else "middle"))
    o.append('<path d="%s" class="s-price" fill="none" stroke-width="2.2" stroke-linejoin="round"/>' % " ".join(
        ("L" if i else "M") + "%.1f %.1f" % (x(t), y(s)) for i, (t, s) in enumerate(P)))
    o.append('<circle cx="%.1f" cy="%.1f" r="5" class="f-price"/>' % (x(P[-1][0]), y(P[-1][1])))
    for t in T["trades"]:
        o.append('<circle cx="%.1f" cy="%.1f" r="7" class="f-%s s-card" stroke-width="2.5"/>' % (
            x(t["t"]), y(t["spot"] if t.get("spot") is not None else p_at(t["t"])), "gain" if t["pl"] >= 0 else "loss"))
    o.append(_line(0, 0, Tp, H - B, "cross s-ink2", ' opacity="0"'))
    for m in range(int(m0), int(m1) + 1, 5):
        tip = "<b>%s</b> · SPX %.1f" % (hhmm(m), p_at(m))
        for t in T["trades"]:
            if abs(t["t"] - m) <= 4:
                tip += "<br>%s %s · %s" % ("✓ Won" if t["pl"] >= 0 else "✕ Lost", money(t["pl"]), _e(t["name"]))
        o.append(_hit(max(L, x(m - 2.5)), min(W - R, x(m + 2.5)), Tp, H - Tp - B, tip, x(m)))
    return "".join(o) + "</svg>"


def _sec_today(T):
    tiles = (_tile("Close", "–" if T["close"] is None else _num(T["close"]), "" if T["chg_pct"] is None else "%+.1f%%" % T["chg_pct"])
             + _tile("Moved", "–" if T["moved"] is None else "%d pts" % round(T["moved"]), "low to high")
             + _tile("Expected", "–" if T["expected"] is None else "±%d" % round(T["expected"]), "pts, by options"))
    if T["checks"]:
        li = []
        for c in T["checks"]:
            mk = ("y", "right", OK) if c["ok"] is True else ("n", "wrong", NO) if c["ok"] is False else ("z", "no call", DASH)
            li.append('<li><span class="mark %s" aria-label="%s">%s</span><div><b>%s</b><span class="tx">%s</span></div></li>' % (
                mk + (_e(c["title"]), _e(c["text"]))))
        checks = "".join(li)
    else:
        checks = '<li><span></span><div class="empty">No checks today.</div></li>'
    if len(T["path"]) < 5:
        chart = '<div class="empty">No price chart for today.</div>'
    else:
        chart = _day_svg(T) + _legend([
            ("width:22px;height:3px;background:var(--price)", "SPX price"),
            ("width:18px;height:12px;background:var(--band)", "Morning close range"),
            ("width:22px;border-top:3px dashed var(--ink2)", "Ceiling / floor"),
            ("width:10px;height:10px;border-radius:50%;background:var(--gain)", "Trade won"),
            ("width:10px;height:10px;border-radius:50%;background:var(--loss)", "Trade lost")])
    return ('<section class="sec4"><h2><span class="n">4</span>Today</h2><div class="tiles">%s</div>'
            '<ul class="checks">%s</ul>%s</section>') % (tiles, checks, chart)


# ------------------------------------------------------------------ page
def render(D):
    gl = "".join("<dt>%s</dt><dd>%s</dd>" % (_e(a), _e(b)) for a, b in GLOSSARY)
    body = ('<main><header class="top"><div class="kick">SPX map · nightly scorecard</div><h1>%s</h1>'
            '<p>How the cockpit\'s map did, in plain words.</p></header>%s%s%s%s'
            '<section class="sec5"><details><summary>What the words mean</summary><dl>%s</dl></details></section>'
            '<p class="foot2">Practice results on real prices, not advice. Open in a browser and tap a chart or row for exact '
            'numbers. The full technical report is kept separately.</p></main><div id="tip"></div>') % (
        _e(D["label"]), _sec_money(D), _sec_strats(D["money"]), _sec_signals(D), _sec_today(D["today"]), gl)
    return ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width,initial-scale=1">\n<title>%s</title>\n<style>%s</style>\n'
            '</head>\n<body>\n%s\n<script>%s</script>\n</body>\n</html>\n') % (
        _e("SPX Map Scorecard · " + D["label"]), CSS, body, JS)


CSS = """
:root{color-scheme:light;--bg:#eef1f5;--card:#ffffff;--ink:#0b0b0b;--ink2:#3d3c39;--line:#d7dce3;--grid:#e8ebef;
  --price:#2a78d6;--band:#cde2fb;--gain:#0f8f84;--loss:#dc3f2c;--wait:#fab219;
  --gainbg:#e3f3f1;--lossbg:#fbe9e6;--waitbg:#fff3d6;--head:#14243a;--headink:#ffffff;
  --s1:#0f8f84;--s2:#2a78d6;--s3:#6250d6;--s4:#14243a}
@media (prefers-color-scheme:dark){:root{color-scheme:dark;--bg:#0f1012;--card:#1a1a19;--ink:#f0efec;--ink2:#c9c8be;--line:#34342f;--grid:#2a2a27;
  --price:#3987e5;--band:#1d3b60;--gain:#14a39a;--loss:#e0533b;--wait:#fab219;
  --gainbg:#10302d;--lossbg:#3a1a14;--waitbg:#3a2f12;--head:#1d3b60;--headink:#ffffff;
  --s1:#14a39a;--s2:#3987e5;--s3:#9085e9;--s4:#5b6b80}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:18px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif}
main{max-width:780px;margin:0 auto;padding:14px 12px 70px}
.top{background:var(--head);color:var(--headink);border-radius:16px;padding:18px 20px;margin-bottom:14px}
.top .kick{font-size:14px;letter-spacing:.12em;text-transform:uppercase;opacity:.85;font-weight:700}
.top h1{margin:2px 0 4px;font-size:30px;line-height:1.15}
.top p{margin:0;font-size:17px;opacity:.92}
section{background:var(--card);border-radius:16px;padding:18px 18px 16px;margin:14px 0;border-top:6px solid var(--sec)}
.sec1{--sec:var(--s1)}.sec2{--sec:var(--s2)}.sec3{--sec:var(--s3)}.sec4{--sec:var(--s4)}.sec5{--sec:var(--line)}
h2{margin:0 0 4px;font-size:25px;line-height:1.2;display:flex;align-items:center;gap:10px}
h2 .n{flex:none;width:34px;height:34px;border-radius:50%;background:var(--sec);color:#fff;font-size:19px;display:flex;align-items:center;justify-content:center}
.lead{margin:0 0 12px;color:var(--ink2);font-size:17px}
.pill{display:inline-flex;align-items:center;gap:6px;font-weight:800;font-size:16px;padding:5px 12px;border-radius:99px;border:2px solid}
.pill.gain{background:var(--gainbg);border-color:var(--gain)}.pill.loss{background:var(--lossbg);border-color:var(--loss)}.pill.wait{background:var(--waitbg);border-color:var(--wait)}
.ic{font-weight:900}.gain .ic{color:var(--gain)}.loss .ic{color:var(--loss)}.wait .ic{color:#b07700}
@media (prefers-color-scheme:dark){.wait .ic{color:var(--wait)}}
.up{color:var(--gain)}.dn{color:var(--loss)}
.hero{display:flex;flex-wrap:wrap;align-items:flex-end;gap:6px 18px;margin:10px 0 4px}
.hero .big{font-size:52px;font-weight:800;line-height:1;letter-spacing:-.02em}
.hero .what{font-size:18px;color:var(--ink2)}
.tiles{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin:12px 0}
.tile{border:1px solid var(--line);border-radius:12px;padding:10px 12px;min-width:0;overflow-wrap:anywhere}
.tile .k{font-size:14px;color:var(--ink2);font-weight:600}
.tile .v{font-size:24px;font-weight:800;line-height:1.2}
.tile .s{font-size:14px;color:var(--ink2)}
.note{font-size:15px;color:var(--ink2);margin:8px 0 0}
svg{display:block;width:100%;height:auto;touch-action:pan-y}
svg text{fill:var(--ink2);font-size:15px;font-family:inherit}
svg text.lab{fill:var(--ink);font-weight:700}
.f-gain{fill:var(--gain)}.f-loss{fill:var(--loss)}.f-price{fill:var(--price)}.f-band{fill:var(--band)}
.s-gain{stroke:var(--gain)}.s-loss{stroke:var(--loss)}.s-price{stroke:var(--price)}.s-grid{stroke:var(--grid)}.s-ink2{stroke:var(--ink2)}.s-card{stroke:var(--card)}
.hit{fill:transparent;stroke:none}
.legend{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:15px;margin:6px 0 2px;color:var(--ink2)}
.legend i{display:inline-block;vertical-align:middle;margin-right:6px}
.brow{padding:10px 0;border-top:1px solid var(--line)}
.brow:first-child{border-top:0}
.bname{font-weight:700;font-size:18px;display:flex;justify-content:space-between;gap:10px}
.bname .amt{font-variant-numeric:tabular-nums;white-space:nowrap}
.bsub{font-size:15px;color:var(--ink2)}
.btrack{position:relative;height:14px;margin-top:4px}
.btrack .mid{position:absolute;left:50%;top:-3px;bottom:-3px;width:2px;background:var(--ink2);opacity:.5}
.btrack .fill{position:absolute;top:0;height:14px}
.btrack .fill.g{left:50%;background:var(--gain);border-radius:0 4px 4px 0}
.btrack .fill.l{right:50%;background:var(--loss);border-radius:4px 0 0 4px}
.axisrow{display:flex;justify-content:space-between;font-size:14px;color:var(--ink2);margin-top:2px}
.rule{font-size:16px;color:var(--ink2);border-left:4px solid var(--sec);padding:2px 0 2px 10px;margin:0 0 12px}
.grp{margin:14px 0 6px;display:flex;flex-wrap:wrap;align-items:center;gap:10px}
.grp .cnt{font-size:15px;color:var(--ink2)}
.cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
.card{border:2px solid var(--line);border-left:8px solid var(--c);border-radius:12px;padding:12px 14px;background:var(--cb);min-width:0}
.card.gain{--c:var(--gain);--cb:var(--gainbg)}.card.loss{--c:var(--loss);--cb:var(--lossbg)}.card.wait{--c:var(--wait);--cb:var(--waitbg)}
.card h3{margin:0 0 2px;font-size:19px}
.card .what{font-size:15px;color:var(--ink2);margin:0 0 8px}
.cmp{display:grid;grid-template-columns:minmax(78px,96px) minmax(0,1fr) 64px;align-items:center;gap:6px;font-size:15px;margin:3px 0}
.cmp .t{height:12px;background:var(--grid);border-radius:4px;overflow:hidden}
.cmp .t b{display:block;height:12px;border-radius:4px}
.cmp .val{text-align:right;font-weight:800;font-variant-numeric:tabular-nums;white-space:nowrap}
.card .foot{font-size:14px;color:var(--ink2);margin-top:6px}
.card .lean{font-size:15px;font-weight:700;margin-top:4px}
.checks{list-style:none;margin:6px 0 12px;padding:0}
.checks li{display:grid;grid-template-columns:34px minmax(0,1fr);gap:8px;padding:9px 0;border-top:1px solid var(--line)}
.checks li:first-child{border-top:0}
.mark{width:30px;height:30px;border-radius:50%;display:flex;align-items:center;justify-content:center;line-height:1}
.mark.y{background:var(--gain)}.mark.n{background:var(--loss)}.mark.z{background:var(--ink2)}
.checks b{font-size:18px}
.checks span.tx{display:block;font-size:15px;color:var(--ink2)}
summary{cursor:pointer;font-weight:700;font-size:18px;padding:4px 0}
dl{margin:6px 0 0}dt{font-weight:800;margin-top:10px}dd{margin:0;color:var(--ink2);font-size:16px}
.foot2{font-size:14px;color:var(--ink2);text-align:center;margin-top:18px}
.empty{font-size:16px;color:var(--ink2);padding:6px 0}
#tip{position:fixed;z-index:9;pointer-events:none;background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:10px;
  padding:8px 11px;font-size:15px;line-height:1.35;box-shadow:0 6px 24px rgba(0,0,0,.18);max-width:260px;display:none}
#tip b{font-size:16px}
@media (max-width:560px){.cards{grid-template-columns:minmax(0,1fr)}.hero .big{font-size:44px}}
@media (max-width:440px){main{padding:10px 8px 60px}section{padding:16px 12px 14px}.top{padding:16px}.top h1{font-size:26px}
  h2{font-size:22px}.tiles{gap:6px}.tile{padding:8px}.tile .v{font-size:20px}.hero .big{font-size:40px}}
"""

# Tap-for-numbers only. Everything else on the page is already written in, so the page is complete without this.
JS = """
(function(){var tip=document.getElementById("tip");if(!tip||!document.addEventListener)return;
function hide(){tip.style.display="none";var c=document.querySelectorAll(".cross");for(var i=0;i<c.length;i++)c[i].setAttribute("opacity","0");}
function show(t,e){var cx=t.getAttribute("data-cx");if(cx!==null){var s=t.closest("svg"),c=s&&s.querySelector(".cross");
  if(c){c.setAttribute("x1",cx);c.setAttribute("x2",cx);c.setAttribute("opacity",".6");}}
  tip.innerHTML=t.getAttribute("data-tip");tip.style.display="block";var w=tip.offsetWidth,h=tip.offsetHeight,L=e.clientX+14,T=e.clientY-h-12;
  if(L+w>innerWidth-8)L=e.clientX-w-14;if(L<8)L=8;if(T<8)T=e.clientY+16;tip.style.left=L+"px";tip.style.top=T+"px";}
function on(e){var t=e.target&&e.target.closest?e.target.closest("[data-tip]"):null;if(t)show(t,e);else hide();}
document.addEventListener("pointermove",on);document.addEventListener("pointerdown",on);})();
"""
