"""risk_page_html.py - created 2026-11-22. Static HTML for risk_page.build() -> risk.html.

Plain words for readers who are not technical; big text (body 16 px, nothing under 14 px); every chart is inline SVG
written at build time, so the page reads fully with scripts off. The only script makes a card big on click / Enter
(Esc closes). Style: project doc claude/zone-card-style-20261122.md.
"""
from __future__ import annotations

import html
import math

MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
RED, AMB, GRN, BLU, INK, BODY, AX = "#B23A2E", "#B7791F", "#2F7D4F", "#3D63C9", "#111418", "#2E3238", "#4A4F57"

CSS = """
*{box-sizing:border-box} body{margin:0;background:#F3F4F6;color:#111418;font:16px/1.45 "IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif}
header{display:flex;flex-wrap:wrap;gap:6px 14px;align-items:baseline;padding:14px 16px 6px}
h1{font-size:24px;margin:0} .asof{font-size:15px;color:#4A4F57}
.links{margin-left:auto;display:flex;gap:8px;flex-wrap:wrap} .links a{font:600 15px system-ui,sans-serif;color:#2B4C9B;text-decoration:none;
 border:1px solid #C9D3EA;background:#fff;padding:5px 12px;border-radius:6px}
.intro{margin:0 16px 12px;font-size:17px;color:#2E3238;max-width:1150px}
.top{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(0,1fr);gap:14px;padding:0 16px}
@media(max-width:900px){.top{grid-template-columns:1fr}}
.box{background:#fff;border:1px solid #DDE0E5;border-radius:8px;padding:16px 18px;min-width:0}
.box h2{margin:0 0 6px;font-size:18px;color:#2E3238;font-weight:600}
.big{font-size:46px;font-weight:700;line-height:1.05;margin:2px 0} .big small{font-size:20px;font-weight:600;margin-left:8px}
.sub{font-size:17px;color:#2E3238;margin:4px 0 10px}
.drv{display:grid;grid-template-columns:minmax(0,1fr) 110px;gap:6px 10px;align-items:center;margin:10px 0 0;font-size:16px}
.bar{height:14px;background:#EEF0F3;border-radius:7px;position:relative;overflow:hidden}
.bar i{position:absolute;top:0;bottom:0;left:0;border-radius:7px}
.phase{display:inline-block;font:700 20px system-ui,sans-serif;padding:4px 12px;border-radius:6px;color:#fff;margin:2px 0 8px}
.meters{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-top:10px}
.meter b{display:block;font-size:26px} .meter span{font-size:15px;color:#2E3238}
.flag{margin:12px 16px 0;padding:12px 16px;border-radius:8px;font-size:17px;border:2px solid}
.grp{margin:26px 16px 10px;border-bottom:2px solid #D4D8DE;padding-bottom:6px}
.grp h3{margin:0;font-size:22px} .grp p{margin:4px 0 0;font-size:16px;color:#2E3238}
.grp .cnt{font:700 15px system-ui,sans-serif;margin-left:10px;color:#B23A2E} .grp .cnt.ok{color:#2F7D4F}
main{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,370px),1fr));gap:14px;padding:0 16px}
.card{background:#fff;border:1px solid #DDE0E5;border-radius:8px;padding:14px 16px;min-width:0;cursor:zoom-in;transition:box-shadow .15s,border-color .15s}
.card:hover,.card:focus{border-color:#8FA6D8;box-shadow:0 3px 14px rgba(30,50,90,.15);outline:none}
.card.big{grid-column:1/-1;cursor:zoom-out;border-color:#3D63C9}
.hd{display:flex;gap:8px;align-items:flex-start} .hd h4{margin:0;font-size:19px;line-height:1.25;flex:1}
.short{font-size:14px;color:#4A4F57;margin-top:2px}
.pill{font:700 14px system-ui,sans-serif;padding:3px 9px;border-radius:12px;white-space:nowrap}
.val{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin:8px 0 4px} .val b{font-size:30px}
.trend{font-size:15px;font-weight:600}
.yr{margin:6px 0 2px;font-size:14px;color:#2E3238} .yrbar{height:12px;border-radius:6px;position:relative;margin:3px 0 2px;
 background:linear-gradient(90deg,#CFE8D8 0%,#EEF0F3 45%,#F6E3C4 75%,#F2C4BE 100%)}
.yrbar.two{background:linear-gradient(90deg,#F2C4BE 0%,#F6E3C4 12%,#EEF0F3 30%,#EEF0F3 70%,#F6E3C4 88%,#F2C4BE 100%)}
.yrbar u{position:absolute;top:-4px;width:4px;height:20px;background:#111418;border-radius:2px;margin-left:-2px}
.yrlab{display:flex;justify-content:space-between;font-size:14px;color:#4A4F57}
svg{display:block;width:100%;height:auto} svg text{font:13px system-ui,sans-serif;fill:#4A4F57} .hist text{font-size:17px}
svg.lg{display:none} .card.big svg.sm{display:none} .card.big svg.lg{display:block}
.key{font-size:14px;color:#4A4F57;display:flex;gap:6px 8px;align-items:center;flex-wrap:wrap;margin-top:2px}
.key .kb{display:inline-block;width:18px;height:10px;background:#E3E6EA} .key .kl{display:inline-block;width:18px;border-top:2px dashed #B23A2E;margin-left:8px}
.card.big .key::after{content:"  (3 years shown)"}
.means{font-size:16px;margin:8px 0 6px;color:#111418} .card.big .means{font-size:18px;max-width:1000px}
.rec{font-size:15px;color:#2E3238;margin:0;border-top:1px solid #E5E7EB;padding-top:8px} .card.big .rec{font-size:17px;max-width:1000px}
.fast{display:inline-block;font:600 14px system-ui,sans-serif;color:#7A4B00;background:#FBEFD5;border-radius:10px;padding:1px 8px;margin-top:4px}
.parts{margin:6px 0;padding:0;list-style:none;font-size:16px} .parts li{margin:3px 0}
.combo{border-left:6px solid;padding-left:12px}
details{margin:12px 16px;background:#fff;border:1px solid #DDE0E5;border-radius:8px;padding:12px 16px}
summary{cursor:pointer;font-size:18px;font-weight:600}
.foot{margin:18px 16px 40px;font-size:15px;color:#2E3238}
"""

JS = """<script>
document.querySelectorAll(".card").forEach(function(c){c.tabIndex=0;c.title="Click to make it big";
 function t(){var on=!c.classList.contains("big");document.querySelectorAll(".card.big").forEach(function(x){x.classList.remove("big")});
  if(on){c.classList.add("big");c.title="Click to make it small";c.scrollIntoView({block:"start",behavior:"smooth"})}else{c.title="Click to make it big"}}
 c.addEventListener("click",function(e){if(e.target.closest("a"))return;t()});c.addEventListener("keydown",function(e){if(e.key==="Enter"||e.key===" "){e.preventDefault();t()}})});
document.addEventListener("keydown",function(e){if(e.key==="Escape")document.querySelectorAll(".card.big").forEach(function(x){x.classList.remove("big")})});
</script>"""


def esc(s) -> str:
    return html.escape(str(s))


def num(v, dec: int = 1) -> str:
    if v is None:
        return "–"
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    return f"{v:,.{dec}f}"


def status(c: dict) -> tuple[str, str, str]:
    """(word, text colour, background) from where the reading sits in its own year (stress side = 1)."""
    r = c.get("rank")
    if r is None:
        return "", "", ""
    if c.get("two"):
        return ("Unusual for this year", "#fff", RED) if r >= 0.9 else ("Unusual", "#5A3600", "#F6D9A8") if r >= 0.75 \
            else ("Normal", "#2E3238", "#E8EAED")
    if r >= 0.9:
        return "Very high for this year", "#fff", RED
    if r >= 0.75:
        return "High", "#5A3600", "#F6D9A8"
    if r >= 0.25:
        return "Normal", "#2E3238", "#E8EAED"
    return "Calm", "#1E5A36", "#D3EDDC"


def trend(c: dict) -> str:
    v, p = c.get("value"), c.get("v2wk")
    if v is None or p is None:
        return ""
    span = (c.get("q75") or 0) - (c.get("q25") or 0)
    d = v - p
    if span <= 0 or abs(d) < 0.25 * span:
        return f'<span class="trend" style="color:{AX}">&#8596; about the same as 2 weeks ago ({num(p, c["dec"])})</span>'
    up = d > 0
    worse = up == bool(c.get("hi_bad", True))
    col = RED if worse else GRN
    word = ("up sharply" if up else "down sharply") if abs(d) >= span else ("up" if up else "down")
    arrow = "&#9650;" if up else "&#9660;"
    return f'<span class="trend" style="color:{col}">{arrow} {word} from {num(p, c["dec"])} 2 weeks ago</span>'


def chart(vals: list, dates: list, c: dict, W: int, H: int, cls: str, days: int) -> str:
    vals, dates = vals[-days:], dates[-days:]
    pts = [(i, v) for i, v in enumerate(vals) if v is not None]
    if len(pts) < 5:
        return ""
    lo = min(v for _, v in pts)
    hi = max(v for _, v in pts)
    for extra in (c.get("line"), c.get("q25"), c.get("q75")):
        if extra is not None:
            lo, hi = min(lo, extra), max(hi, extra)
    pad = (hi - lo) * 0.08 or 1
    lo, hi = lo - pad, hi + pad
    PL, PR, PT, PB = 44, 10, 10, 24
    iw, ih, n = W - PL - PR, H - PT - PB, len(vals)
    X = lambda i: PL + iw * i / max(n - 1, 1)  # noqa: E731
    Y = lambda v: PT + ih * (hi - v) / (hi - lo)  # noqa: E731
    o = [f'<svg class="{cls}" viewBox="0 0 {W} {H}" role="img" aria-label="{esc(c["title"])} trend">']
    if c.get("q25") is not None and c.get("q75") is not None:
        o.append(f'<rect x="{PL}" y="{Y(c["q75"]):.1f}" width="{iw}" height="{max(1, Y(c["q25"]) - Y(c["q75"])):.1f}" fill="#EEF0F3"/>')
    if c.get("line") is not None:
        o.append(f'<line x1="{PL}" x2="{W - PR}" y1="{Y(c["line"]):.1f}" y2="{Y(c["line"]):.1f}" stroke="{RED}" '
                 f'stroke-dasharray="6 4" stroke-width="1.4"/>')
    for v in (hi - pad, lo + pad):
        o.append(f'<text x="{PL - 6}" y="{Y(v) + 4:.1f}" text-anchor="end">{num(v, c["dec"])}</text>')
    seen, step = set(), (1, 4, 7, 10) if days <= 300 else (1, 7)
    for i, s in enumerate(dates):
        if i > 3 and len(s) >= 7 and int(s[5:7]) in step and s[:7] not in seen and dates[i - 1][:7] != s[:7]:
            seen.add(s[:7])
            lab = MON[int(s[5:7]) - 1] + ("" if days <= 300 else " " + s[2:4])
            o.append(f'<text x="{X(i):.1f}" y="{H - 6}" text-anchor="middle">{lab}</text>')
    path, pen = [], False
    for i, v in enumerate(vals):
        if v is None:
            pen = False
            continue
        path.append(("L" if pen else "M") + f"{X(i):.1f},{Y(v):.1f}")
        pen = True
    o.append(f'<path d="{" ".join(path)}" fill="none" stroke="{BLU}" stroke-width="{1.7 if cls == "sm" else 2}" stroke-linejoin="round"/>')
    li, lv = pts[-1]
    o.append(f'<circle cx="{X(li):.1f}" cy="{Y(lv):.1f}" r="4.5" fill="{BLU}"/>')
    o.append("</svg>")
    return "".join(o)


def record(c: dict) -> str:
    rid = c["id"]
    if rid == "kurt_bear":
        rt = c.get("rates") or {}
        k = str(int(c.get("value") or 0))
        r = rt.get(k)
        return (f"<b>When {k} of the 4 parts were on,</b> the S&amp;P 500 fell 3% or more within a week about "
                f"{round(100 * r)} times in 100 (normal is about 9). All 4 on: about {round(100 * rt.get('4', 0))} in 100."
                if r is not None else "")
    if c.get("role") in ("untested",) or rid in ("our_spx_gamma", "our_mag7_neg"):
        return f"<b>Too new to judge.</b> Our own gamma history starts in May 2026. {esc(c.get('verdict', ''))}."
    if c.get("role") == "short":
        return "<b>Only about 3 years of data,</b> so we cannot say yet how good a warning it is."
    rate, base = c.get("rate"), c.get("base") or 0.09
    if rate is None:
        return "<b>Not enough past readings this extreme</b> to say what usually came next."
    side = "most stressed" if c.get("hi_bad", True) or c.get("src") == "r" else "lowest"
    ratio = rate / base if base else 0
    how = "a useful warning" if ratio >= 2 else "a mild warning" if ratio >= 1.4 else "not much of a warning on its own"
    lead = "One of the three readings in the main score. " if c.get("role") == "score" else ""
    return (f"{lead}<b>When this was in the {side} 10% of its year,</b> the S&amp;P 500 fell 3% or more within a week "
            f"about {round(100 * rate)} times in 100. Normal is about {round(100 * base)}. So it has been {how}.")


def card_html(c: dict) -> str:
    word, fg, bg = status(c)
    pill = f'<span class="pill" style="color:{fg};background:{bg}">{word}</span>' if word else ""
    unit = esc(c.get("unit") or "")
    val = ""
    if c["id"] == "kurt_bear":
        on = sum(1 for p in c.get("parts") or [] if p.get("bear"))
        val = f'<div class="val"><b>{on} of 4</b><span class="trend" style="color:{RED if on >= 3 else AX}">parts on</span></div>'
        val += '<ul class="parts">' + "".join(
            f'<li>{"&#128308;" if p.get("bear") else "&#9898;"} {esc(p.get("name"))}</li>' for p in c.get("parts") or []) + "</ul>"
    elif c["id"] == "our_spx_gamma":
        fd = c.get("flip_dist")
        where = "" if fd is None else (f"S&amp;P 500 is {abs(float(fd)):.1f}% {'below' if float(fd) < 0 else 'above'} the flip line ({esc(c.get('flip'))})")
        val = f'<div class="val"><b>{num(c.get("value"), 0)}</b><span class="trend">{where}</span></div>'
    elif c["id"] == "our_mag7_neg":
        val = f'<div class="val"><b>{num(c.get("value"), 0)} of {esc(c.get("of", 7))}</b></div>'
    else:
        val = f'<div class="val"><b>{num(c.get("value"), c["dec"])}{unit}</b>{trend(c)}</div>'
    yr = ""
    if c.get("rank") is not None:
        two = bool(c.get("two"))
        r = c.get("raw", c["rank"]) if two else c["rank"]
        lab = "<span>low</span><span>high</span>" if two else "<span>calm</span><span>stressed</span>"
        yr = (f'<div class="yr">Where it sits in its own past year</div><div class="yrbar{" two" if two else ""}">'
              f'<u style="left:{100 * r:.0f}%"></u></div><div class="yrlab">{lab}</div>')
    fast = '<span class="fast">Moved unusually fast in the last 2 months</span>' if c.get("fast") else ""
    sm = chart(c.get("vals") or [], c.get("dates") or [], c, 420, 170, "sm", 252)
    lg = chart(c.get("vals") or [], c.get("dates") or [], c, 1150, 330, "lg", 756)
    key = ""
    if sm:
        key = ('<div class="key"><span class="kb"></span>normal range for the year'
               + ('<span class="kl"></span>warning line' if c.get("line") is not None else "") + "</div>")
    return (f'<section class="card"><div class="hd"><div style="flex:1"><h4>{esc(c["title"])}</h4>'
            f'<div class="short">{esc(c["short"])}</div></div>{pill}</div>{val}{yr}{fast}{sm}{lg}{key}'
            f'<p class="means">{esc(c["means"])}</p><p class="rec">{record(c)}</p></section>')


def verdict(o: dict) -> str:
    s = o.get("score") or {}
    p5 = s.get("p5")
    if p5 is None:
        return '<div class="box"><h2>Chance of a sharp drop</h2><p class="sub">Not built yet tonight.</p></div>'
    base = s.get("base5") or 0.09
    col = RED if p5 >= 0.25 else "#C25E00" if p5 >= 0.15 else AMB if p5 >= 0.06 else GRN
    names = {"vix": "Fear gauge (VIX) level", "vix_vix3m": "Fear now vs next 3 months", "nfci": "Financial stress (Chicago Fed)"}
    drv = ""
    for inp, cb in zip(s.get("inputs") or [], s.get("contrib") or []):
        w = max(0.0, min(1.0, float(cb or 0)))
        drv += (f'<div>{names.get(inp["id"], esc(inp["label"]))}: <b>{esc(inp["value"])}</b></div>'
                f'<div class="bar"><i style="width:{100 * w:.0f}%;background:{col}"></i></div>')
    sim = s.get("similar") or {}
    simt = (f'<p class="sub">On {sim["n"]} past days with a reading like today, a 3%+ drop followed {round(100 * float(sim["drop_rate"]))}'
            f' times in 100. The usual worst close that week was {sim["median_worst5"]}%, a bad case {sim["p10_worst5"]}%.</p>'
            if sim.get("n") else "")
    p10 = s.get("p10")
    return (f'<div class="box"><h2>Chance the S&amp;P 500 falls 3% or more within a week</h2>'
            f'<div class="big" style="color:{col}">{round(100 * p5)}%<small>{esc(s.get("band_label") or "")}</small></div>'
            f'<p class="sub">Normal is about {round(100 * base)}%, so today is about {p5 / base:.1f}&times; normal.'
            + (f' A 5%+ drop within two weeks: {round(100 * p10)}% (normal {round(100 * (s.get("base10") or 0.067))}%).' if p10 is not None else "")
            + f'</p><div style="font-size:16px;font-weight:600">What is pushing it (bigger bar = pushing harder)</div>'
            f'<div class="drv">{drv}</div>{simt}{hist_chart(o)}</div>')


def hist_chart(o: dict) -> str:
    h = [r for r in (o.get("hist") or [])[-252:] if r[2] is not None]
    if len(h) < 20:
        return ""
    W, H, PL, PR, PT, PB = 640, 170, 40, 10, 10, 24
    iw, ih, n = W - PL - PR, H - PT - PB, len(h)
    top = max(0.35, max(float(r[2]) for r in h) * 1.1)
    X = lambda i: PL + iw * i / (n - 1)  # noqa: E731
    Y = lambda v: PT + ih * (1 - v / top)  # noqa: E731
    sp = [float(r[1]) for r in h if r[1] is not None]
    slo, shi = min(sp), max(sp)
    YS = lambda v: PT + ih * (1 - (v - slo) / ((shi - slo) or 1))  # noqa: E731
    o_ = [f'<div style="font-size:16px;font-weight:600;margin-top:12px">The last year: drop chance (colour) and the S&amp;P 500 (grey)</div>'
          f'<svg class="hist" viewBox="0 0 {W} {H}" role="img" aria-label="drop chance, last year">']
    o_.append(f'<path d="' + " ".join(("M" if i == 0 else "L") + f"{X(i):.1f},{YS(float(r[1])):.1f}" for i, r in enumerate(h) if r[1] is not None)
              + '" fill="none" stroke="#B5BAC2" stroke-width="1.5"/>')
    o_.append(f'<line x1="{PL}" x2="{W - PR}" y1="{Y(0.09):.1f}" y2="{Y(0.09):.1f}" stroke="{AX}" stroke-dasharray="5 4"/>')
    o_.append(f'<text x="{W - PR - 4}" y="{Y(0.09) - 4:.1f}" text-anchor="end">normal 9%</text>')
    for v in (0.0, 0.25):
        if v <= top:
            o_.append(f'<text x="{PL - 6}" y="{Y(v) + 4:.1f}" text-anchor="end">{round(100 * v)}%</text>')
    for i in range(1, n):
        v = float(h[i][2])
        col = RED if v >= 0.25 else "#C25E00" if v >= 0.15 else AMB if v >= 0.06 else GRN
        o_.append(f'<line x1="{X(i - 1):.1f}" y1="{Y(float(h[i - 1][2])):.1f}" x2="{X(i):.1f}" y2="{Y(v):.1f}" stroke="{col}" stroke-width="2.2"/>')
    seen = set()
    for i, r in enumerate(h):
        s = r[0]
        if i > 3 and int(s[5:7]) in (1, 4, 7, 10) and s[:7] not in seen and h[i - 1][0][:7] != s[:7]:
            seen.add(s[:7])
            o_.append(f'<text x="{X(i):.1f}" y="{H - 6}" text-anchor="middle">{MON[int(s[5:7]) - 1]}</text>')
    o_.append("</svg>")
    return "".join(o_)


def fuse_box(o: dict) -> str:
    f = o.get("fuse")
    if not f:
        return '<div class="box"><h2>Are stocks moving as one?</h2><p class="sub">The correlation fuse is not built yet tonight.</p></div>'
    from risk_page import PHASE
    word, text, col = PHASE.get(f["phase"], (f["phase"], "", AX))
    w5 = PHASE.get(f.get("phase5"), (f.get("phase5"), "", AX))[0]
    sc, s5, cnt = f["score"], f.get("score5", {}), f.get("count", {})
    lab = {"F": ("Risk building", "fuse loaded"), "T": ("Move happening", "stress reaching the index"),
           "R": ("Move ending", "stress fading")}
    meters = "".join(
        f'<div class="meter"><span>{lab[g][0]}</span><b>{sc[g]}<span> of {cnt.get(g, "?")}</span></b>'
        f'<div class="bar"><i style="width:{100 * sc[g] / max(1, cnt.get(g, 1)):.0f}%;background:{col}"></i></div>'
        f'<span>5 days ago: {s5.get(g, "–")}</span></div>' for g in ("F", "T", "R"))
    return (f'<div class="box"><h2>Are stocks moving as one? (correlation fuse)</h2><span class="phase" style="background:{col}">{esc(word)}</span>'
            f'<p class="sub">{esc(text)} Five days ago it read: {esc(w5)}.</p><div class="meters">{meters}</div>'
            f'<p class="sub" style="margin-top:12px">22 checks on whether stocks move together. '
            f'<a href="fuse.html">See all 22 &rarr;</a></p></div>')


def flags(o: dict) -> str:
    f = o.get("flags") or {}
    out = ""
    fa, fr = f.get("false_allclear") or {}, f.get("fear_release") or {}
    if fa.get("on"):
        out += (f'<div class="flag" style="border-color:{AMB};background:#FFF7E6"><b>Careful: false all-clear.</b> Fear dropped sharply '
                f'in one day right after a stressed week. A 3%+ drop still followed about {round(100 * fa.get("rate", 0))} times in 100.</div>')
    if fr.get("on"):
        out += (f'<div class="flag" style="border-color:{GRN};background:#EEF8F1"><b>Fear is releasing.</b> VIX is well off its recent high '
                f'and back under 3-month fear. The S&amp;P 500 was higher 20 days later about {round(100 * fr.get("up20", 0))} times in 100.</div>')
    return out


def combos(o: dict) -> str:
    cs = o.get("combos") or []
    if not cs:
        return ""

    def one(c):
        st = c.get("stats") or {}
        tx = (f'Past days like this since 2010 ({st.get("episodes", "?")} separate times): the S&amp;P 500 was higher 20 days later '
              f'{round(100 * st.get("up20", 0))} times in 100, and fell 3%+ within a week {round(100 * st.get("drop5", 0))} times in 100.'
              if st else "")
        return (f'<section class="card combo" style="border-left-color:{esc(c.get("color", AX))}"><div class="hd"><h4>{esc(c.get("title"))}</h4>'
                f'{"<span class=pill style=color:#fff;background:" + RED + ">On now</span>" if c.get("on") else ""}</div>'
                f'<p class="means">{esc(c.get("meaning", ""))}</p><p class="rec">{tx}'
                + (f' <b>What would calm it:</b> {esc(c.get("watch"))}' if c.get("watch") else "") + "</p></section>")
    on = [c for c in cs if c.get("on")]
    off = [c for c in cs if not c.get("on")]
    head = (f'<div class="grp"><h3>Warning combinations<span class="cnt{"" if on else " ok"}">{len(on)} of {len(cs)} on</span></h3>'
            f'<p>Some readings mean more together. These are the mixes we track.</p></div>')
    return (head + ("<main>" + "".join(one(c) for c in on) + "</main>" if on else "")
            + (f'<details><summary>The {len(off)} combinations that are off now</summary><main style="padding:10px 0 0">'
               + "".join(one(c) for c in off) + "</main></details>" if off else ""))


def page(o: dict) -> str:
    secs = []
    for gid, q, about in o.get("groups") or []:
        cs = [c for c in o["cards"] if c["group"] == gid]
        if not cs:
            continue
        hot = sum(1 for c in cs if (c.get("rank") or 0) >= 0.9)
        cnt = f"{hot} of {len(cs)} very high for their year" if hot else "none very high"
        secs.append(f'<div class="grp"><h3>{esc(q)}<span class="cnt{"" if hot else " ok"}">{cnt}</span></h3><p>{esc(about)}</p></div>'
                    f'<main>{"".join(card_html(c) for c in cs)}</main>')
    asof = o.get("asof_risk") or o.get("asof_fuse") or ""
    intro = ("This page answers one question: <b>how likely is a sharp drop in the S&amp;P 500 soon?</b> The big number is "
             "the tested answer. The cards under it show each warning sign on its own: where it sits compared with its own "
             "past year, which way it is heading, and what usually happened next. Click any card to make it big. "
             "Nothing here is a buy or sell signal.")
    return (f'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>Risk</title><!-- risk.html - written by scripts/risk_page.py ({esc(o.get("built"))}); display only (rule 4). -->'
            f'<style>{CSS}</style></head><body><header><h1>Risk</h1><span class="asof">closing data of {esc(asof)}</span>'
            f'<span class="links"><a href="fuse.html">Correlation fuse detail</a><a href="zones.html">Speed view (z-scores)</a></span></header>'
            f'<p class="intro">{intro}</p>{flags(o)}<div class="top">{verdict(o)}{fuse_box(o)}</div>'
            f'{"".join(secs)}{combos(o)}'
            f'<p class="foot">How it was built: the drop chance is a model of three readings (fear level, fear now vs 3 months, '
            f'Chicago Fed financial stress), trained and tested on 2010&ndash;2026 data, each half checked on years it never saw. '
            f'Every card&rsquo;s &ldquo;what usually came next&rdquo; counts the same event: the S&amp;P 500 closing 3% or more below the day&rsquo;s close '
            f'at some point in the next 5 trading days. Built {esc(o.get("built"))}.</p>{JS}</body></html>')
