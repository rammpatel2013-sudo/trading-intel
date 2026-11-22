"""zone_cards_html.py - created 2026-11-22. Static HTML for zone_cards.build() output -> zones.html.

Every number and chart is written in at build time (inline SVG, no scripts), so the page reads fully inside the
cockpit's Fuse tab, on its own at :8899/zones.html, and in a phone preview. Style: project doc
claude/zone-card-style-20261122.md (light card, pale zone bands, one blue line; text 13 px minimum, body 15 px).
"""
from __future__ import annotations

import html
import math

W, H, PL, PR, PT, PB = 400, 220, 30, 10, 12, 24   # chart box (viewBox units)
MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

CSS = """
:root{--ink:#111418;--body:#33373D;--axis:#5E6268;--line:#5878C8;--sellb:#F9F0F1;--sell:#B8788A;--buyb:#F1F8F8;
 --buy:#4F7F72;--neg:#B4533A;--pos:#3F7A4E;--grid:#E6E6E6;--sans:"IBM Plex Sans",system-ui,-apple-system,sans-serif;
 --serif:Georgia,"Source Serif 4","Times New Roman",serif;--mono:"IBM Plex Mono",ui-monospace,Menlo,Consolas,monospace}
*{box-sizing:border-box} body{margin:0;background:#F4F5F7;color:var(--ink);font:15px var(--sans)}
header{display:flex;flex-wrap:wrap;gap:6px 16px;align-items:baseline;padding:14px 16px 8px}
h1{font-size:22px;margin:0} .asof{font:14px var(--mono);color:var(--axis)} a.back{margin-left:auto;font:600 15px var(--sans);
 color:#2B4C9B;text-decoration:none;border:1px solid #C9D3EA;background:#fff;padding:5px 12px;border-radius:6px}
.how{margin:0 16px 12px;font-size:16px;line-height:1.5;color:var(--body);max-width:1100px}
main{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,360px),1fr));gap:14px;padding:0 16px 8px}
.card{background:#fff;border:1px solid #E1E1E1;border-radius:6px;padding:14px 16px;min-width:0;cursor:zoom-in;
 transition:box-shadow .15s,border-color .15s} .card:hover{border-color:#9FB3D9;box-shadow:0 3px 12px rgba(30,50,90,.14)}
.card.big{grid-column:1/-1;cursor:zoom-out;border-color:#5878C8;box-shadow:0 4px 18px rgba(30,50,90,.18)}
svg.lg{display:none} .card.big svg.sm{display:none} .card.big svg.lg{display:block} .card.big .verdict{font-size:20px} .card.big .hist{font-size:17px;max-width:1100px}
.tipx{font-size:14px;color:var(--axis);margin-left:auto}
.top{display:flex;flex-wrap:wrap;gap:6px;align-items:center} .top h2{font-size:22px;margin:0 auto 0 0}
.chip{font:600 13px var(--mono);letter-spacing:.04em;padding:3px 8px;border-radius:4px;white-space:nowrap}
.b-zone-hi{background:#A8473A;color:#fff} .b-zone-lo{background:#487850;color:#fff}
.b-near-hi{border:1.5px solid #8A4A3C;color:#8A4A3C;background:#fff} .b-near-lo{border:1.5px solid #4F6E52;color:#4F6E52;background:#fff}
.b-none{background:#F0F2F6;color:#4A4D52} .r-strong{background:#E3F1E8;color:#2F5E44} .r-some{background:#E8EEF8;color:#2B4C9B}
.r-weak{background:#F6EFC8;color:#6E5214;font-family:var(--sans);letter-spacing:0}
.meta{font:14px var(--mono);color:var(--axis);margin:8px 0 4px;display:flex;flex-wrap:wrap;gap:4px 14px}
.meta b{color:var(--ink);font-weight:600} .neg{color:var(--neg)} .pos{color:var(--pos)}
svg{display:block;width:100%;height:auto} svg text{font:13px var(--mono);fill:var(--axis)}
svg .call{font-weight:700;fill:var(--ink);paint-order:stroke;stroke:#fff;stroke-width:4px}
hr{border:0;border-top:1px solid #E1E1E1;margin:8px 0 10px}
.verdict{font:18px/1.4 var(--serif);margin:0 0 8px} .verdict b{font-weight:700}
.hist{font-size:15px;line-height:1.5;color:var(--body);margin:0}
.grp{margin:18px 16px 8px;display:flex;flex-wrap:wrap;gap:4px 14px;align-items:baseline;border-bottom:2px solid #D9DDE3;padding-bottom:6px}
.grp h3{margin:0;font-size:20px} .grp .cnt{font:600 14px var(--mono);color:#A8473A} .grp .cnt.ok{color:#3F7A4E}
.grp p{flex-basis:100%;margin:2px 0 0;font-size:15px;color:var(--body)} main+.grp{margin-top:24px}
.fuse{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:0 16px 12px;padding:10px 14px;background:#fff;
 border:1px solid #E1E1E1;border-radius:6px} .fuse .ph{font:700 16px var(--mono);letter-spacing:.06em;padding:3px 10px;
 border-radius:4px;border:2px solid currentColor} .ph-QUIET{color:#3F7A4E} .ph-LOADED{color:#A06A10}
 .ph-TRANSMITTING{color:#A8473A} .ph-RELEASING{color:#2B6C9B} .fuse .sc{font:15px var(--mono)} .fuse .sc b{font-size:20px}
 .fuse .lab{font-size:15px;color:var(--body)}
"""


JS = """<script>
/* click (or Enter) a card to make it full width; click again / Esc to shrink. Page reads fully without this. */
document.querySelectorAll(".card").forEach(function(c){c.tabIndex=0;c.title="Click to enlarge";
 function t(){var on=!c.classList.contains("big");document.querySelectorAll(".card.big").forEach(function(x){x.classList.remove("big")});
  if(on){c.classList.add("big");c.title="Click to shrink";c.scrollIntoView({block:"start",behavior:"smooth"})}else{c.title="Click to enlarge"}}
 c.addEventListener("click",t);c.addEventListener("keydown",function(e){if(e.key==="Enter"||e.key===" "){e.preventDefault();t()}})});
document.addEventListener("keydown",function(e){if(e.key==="Escape")document.querySelectorAll(".card.big").forEach(function(x){x.classList.remove("big")})});
</script>"""


def esc(s) -> str:
    return html.escape(str(s))


def fmt(v: float) -> str:
    a = abs(v)
    return f"{v:,.0f}" if a >= 100 else f"{v:.1f}" if a >= 10 else f"{v:.2f}"


def sig(z: float) -> str:
    return f"{z:+.2f}σ"


def chart(c: dict, W: int = W, H: int = H, cls: str = "sm") -> str:
    d, z = c["chart"]["d"], c["chart"]["z"]
    n = len(d)
    zmax = min(6.0, max(3.0, math.ceil(max(abs(v) for v in z if v is not None) + 0.3)))
    iw, ih = W - PL - PR, H - PT - PB
    X = lambda i: PL + iw * i / max(n - 1, 1)  # noqa: E731
    Y = lambda v: PT + ih * (zmax - max(-zmax, min(zmax, v))) / (2 * zmax)  # noqa: E731
    o = [f'<svg class="{cls}" viewBox="0 0 {W} {H}" role="img" aria-label="{esc(c["title"])} z-score, last year">',
         f'<rect x="{PL}" y="{PT}" width="{iw}" height="{Y(2) - PT:.1f}" fill="var(--sellb)"/>',
         f'<rect x="{PL}" y="{Y(-2):.1f}" width="{iw}" height="{PT + ih - Y(-2):.1f}" fill="var(--buyb)"/>',
         f'<line x1="{PL}" x2="{W - PR}" y1="{Y(0):.1f}" y2="{Y(0):.1f}" stroke="var(--grid)"/>']
    for lv, col, dash in ((2, "var(--sell)", "6 4"), (1.5, "var(--sell)", "1.5 3"), (-1.5, "var(--buy)", "1.5 3"),
                          (-2, "var(--buy)", "6 4")):
        o.append(f'<line x1="{PL}" x2="{W - PR}" y1="{Y(lv):.1f}" y2="{Y(lv):.1f}" stroke="{col}" '
                 f'stroke-dasharray="{dash}" stroke-width="1.2"/>')
    zl = f"{zmax:g}"
    for lv, lab in ((zmax, "+" + zl), (2, "+2"), (0, "0"), (-2, "−2"), (-zmax, "−" + zl)):
        o.append(f'<text x="{PL - 6}" y="{Y(lv) + 4:.1f}" text-anchor="end">{lab}</text>')
    o.append(f'<text x="{PL + 4}" y="{PT + 14}" style="fill:#8A4A5A">high zone</text>')
    o.append(f'<text x="{PL + 4}" y="{PT + ih - 5}" style="fill:#3F6E60">low zone</text>')
    seen = set()
    for i, s in enumerate(d):
        m = int(s[5:7])
        if m in (1, 4, 7, 10) and (s[:7]) not in seen and i > 3 and (i == 0 or d[i - 1][:7] != s[:7]):
            seen.add(s[:7])
            o.append(f'<line x1="{X(i):.1f}" x2="{X(i):.1f}" y1="{PT + ih}" y2="{PT + ih + 4}" stroke="var(--axis)"/>')
            o.append(f'<text x="{X(i):.1f}" y="{H - 6}" text-anchor="middle">{MON[m - 1]}</text>')
    path, pen = [], False
    for i, v in enumerate(z):
        if v is None:
            pen = False
            continue
        path.append(("L" if pen else "M") + f"{X(i):.1f},{Y(v):.1f}")
        pen = True
    o.append(f'<path d="{" ".join(path)}" fill="none" stroke="var(--line)" stroke-width="1.6" stroke-linejoin="round"/>')
    li = max((i for i, v in enumerate(z) if v is not None), default=None)
    if li is not None:
        lab = {"zone": "Zone", "near": "Nearing", "none": "No signal"}[c["state"]]
        yv = Y(z[li])
        ty = yv - 9 if yv > PT + 22 else yv + 20
        o.append(f'<circle cx="{X(li):.1f}" cy="{yv:.1f}" r="4" fill="var(--line)"/>')
        o.append(f'<text class="call" x="{X(li) - 8:.1f}" y="{ty:.1f}" text-anchor="end">{sig(z[li])} · {lab}</text>')
    o.append("</svg>")
    return "".join(o)


def words(c: dict) -> tuple[str, str, str]:
    """(bold lead, second sentence, history paragraph) in plain words."""
    z, st_, side, h, b = c["z"], c["state"], c["side"], c["hist"], c["base"]
    hi = side > 0
    where = ("stretched high" if hi else "stretched low") if st_ == "zone" else \
            ("nearing its high zone" if hi else "nearing its low zone") if st_ == "near" else "inside its normal range"
    if st_ == "none":
        lead = "Normal range."
    elif c["record"] == "weak" or not h:
        lead = "No history edge at this level, so a warning, not a trigger." if hi else \
               "No history edge at this level, so a setup, not a trigger."
    elif c["record_on"] == "drop":
        lead = "Big drops have followed more often than usual." if h["drop"] > b["drop"] else \
               "Big drops have followed less often than usual."
    else:
        lead = "SPX has tended to rise after this." if h["up"] > b["up"] else "SPX has tended to fall after this."
    second = (f"{c['title']} {fmt(c['value'])} vs a 50-day average of {fmt(c['mean50'])}: {where} ({sig(z)}). "
              f"{c['meaning']}." if st_ != "none" else
              f"{c['title']} {fmt(c['value'])} vs a 50-day average of {fmt(c['mean50'])} ({sig(z)}).")
    lv = c["level"]
    word = "above +" if hi else "below −"
    zone_txt = f"{word}{lv:g}σ"
    if not h:
        return lead, second, f"It has not gone {zone_txt} since {c['chart']['d'][0][:4]} often enough to say anything."
    pre = "For reference: after" if st_ == "none" else "After"
    back = {"strong": "History backs it.", "some": "History leans this way, but the sample is small.",
            "weak": "History does not back it, so treat it as context, not a trade."}[c["record"]]
    hist = (f"{pre} the {h['n']} past times it went {zone_txt} since 2014, SPX rose over the next 20 days "
            f"{h['up']:g}% of the time (avg {h['avg']:+.1f}%) and fell 5%+ at some point {h['drop']:g}% of the time, "
            f"against {b['up']:g}% (avg {b['avg']:+.1f}%) and {b['drop']:g}% for any 20 days. {back}")
    return lead, second, hist


def card_html(c: dict) -> str:
    hl = "hi" if c["side"] > 0 else "lo"
    badge = {"zone": ("High zone" if c["side"] > 0 else "Low zone"),
             "near": ("Nearing high" if c["side"] > 0 else "Nearing low"), "none": "No signal"}[c["state"]]
    bcls = f"b-{c['state']}-{hl}" if c["state"] != "none" else "b-none"
    lead, second, hist = words(c)
    z2 = "" if c["z_2wk"] is None else f"<span>Two weeks ago <b>{sig(c['z_2wk'])}</b></span>"
    dv = c["value"] - c["mean50"]
    return (f'<section class="card"><div class="top"><h2>{esc(c["title"])}</h2>'
            f'<span class="chip {bcls}">{badge} · {sig(c["z"])}</span>'
            f'<span class="chip r-{c["record"]}">{c["record"]} record</span></div>'
            f'<div class="meta"><span>Now <b>{fmt(c["value"])}</b></span><span>50-day avg <b>{fmt(c["mean50"])}</b>'
            f' <span class="{"neg" if dv > 0 else "pos"}">({dv:+.2f})</span></span>{z2}</div>'
            f'{chart(c)}{chart(c, 1100, 340, "lg")}<hr><p class="verdict"><b>{esc(lead)}</b> {esc(second)}</p><p class="hist">{esc(hist)}</p>'
            f'</section>')


def fuse_strip(f: dict | None) -> str:
    """The Fuse score line (from fuse_tab's compute output) - phase + Fragility / Transmission / Release now and 5 days ago."""
    if not f or "score" not in f:
        return ""
    sc, s5, n = f["score"], f.get("score5", {}), f.get("count", {})
    names = {"F": "Fragility", "T": "Transmission", "R": "Release"}
    cells = "".join(f'<span class="sc">{names[g]} <b>{sc[g]}</b>/{n.get(g, "?")} '
                    f'<span class="lab">(5d ago {s5.get(g, "-")})</span></span>' for g in ("F", "T", "R"))
    return (f'<div class="fuse"><span class="lab">Correlation Fuse</span>'
            f'<span class="ph ph-{esc(f["phase"])}">{esc(f["phase"])}</span>{cells}</div>')


def page(o: dict, fuse: dict | None = None) -> str:
    b = o["base"]
    how = ("Each card asks one question: is this gauge far from its own last 50 days? The line is that distance in "
           "standard deviations (σ). Past +2σ or −2σ it sits in a shaded zone. Under the chart, "
           "the grey paragraph says what SPX did over the next 20 sessions the past times the gauge entered the same "
           f"zone, next to any 20 sessions ({b['up']:g}% up, avg {b['avg']:+.1f}%, a 5%+ drop {b['drop']:g}% of the "
           "time). Click any card to make it big. <b>strong record</b> = that difference held up; <b>weak record</b> = it did not, so the zone is "
           "context, not a trade.")
    secs = []
    for g in o.get("groups") or [{"id": None, "title": "", "about": ""}]:
        cs = [c for c in o["cards"] if g["id"] is None or c.get("group") == g["id"]]
        if not cs:
            continue
        nz = sum(c["state"] == "zone" for c in cs)
        nn = sum(c["state"] == "near" for c in cs)
        cnt = (f"{nz} of {len(cs)} in a zone" + (f", {nn} nearing" if nn else "")) if nz or nn else f"all {len(cs)} normal"
        head = (f'<div class="grp"><h3>{esc(g["title"])}</h3><span class="cnt{"" if nz or nn else " ok"}">{cnt}</span>'
                f'<p>{esc(g["about"])}</p></div>') if g["id"] else ""
        secs.append(head + "<main>" + "".join(card_html(c) for c in cs) + "</main>")
    cards = "".join(secs)
    return (f'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width, initial-scale=1"><title>Zone cards</title>'
            f'<!-- zones.html - written by scripts/zone_cards.py ({esc(o["built"])}); display only (rule 4). -->'
            f'<style>{CSS}</style></head><body><header><h1>Zone cards</h1>'
            f'<span class="asof">closes to {esc(o["asof"])} · history from {esc(o["start"][:4])}</span>'
            f'<a class="back" href="fuse.html">← Fuse score</a></header>{fuse_strip(fuse)}<p class="how">{how}</p>'
            f'{cards}{JS}</body></html>')
