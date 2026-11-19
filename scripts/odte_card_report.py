#!/usr/bin/env python
# ruff: noqa: E501, N806, RUF001, RUF100
"""odte_card_report.py - created 2026-11-14 for Mithil. The SPX 0DTE map card as one phone page.

Learned grammar (project doc claude/card-visual-grammar-20261114.md): price axis to scale, colour = regime only,
three line weights (wall / step / gap box), close odds as a rail ON the price axis plus one bar, 50% / 80%
close ranges as brackets, if/then rows keyed by the same up / range / down marks, every number's source at the
bottom. Data: trading_intel.market.odte_card (one book + one spot). Python writes every number and the map
(inline SVG) at build time - no scripts, so Telegram's phone preview shows all of it.

    python scripts/odte_card_report.py                 # 08:45: today's 08:35 book -> html + png + Telegram
    python scripts/odte_card_report.py --live          # 09:45: one live chain pull, "since 08:35" block
    python scripts/odte_card_report.py --as-of 2026-11-13 --no-telegram
NAS (DSM): sudo bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_script.sh odte_card_report
Descriptor, not a signal (rule 4).
"""
from __future__ import annotations

import argparse
import html
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

OUT_DIR = REPO / "reports" / "odte_card"
INK, RUST, TEAL, AMBER = "#15212f", "#a8461f", "#1f7a6c", "#c48a1c"
TINT = {"rust": "#f0d5c9", "teal": "#d6ece7", "ink": "#e3e8ef", "amber": "#f6ead0"}
E = html.escape
MARK = {"up": ("▲", TEAL), "range": ("◆", INK), "down": ("▼", RUST)}


def f0(x: float | None) -> str:
    return "–" if x is None else f"{x:,.0f}"


# ------------------------------------------------------------------ the map (inline SVG, price to scale)
def spread_labels(ys: list[float], gap: float, lo: float, hi: float) -> list[float]:
    """Nudge label rows apart (>= ``gap``) keeping their order, inside [lo, hi]."""
    order = sorted(range(len(ys)), key=lambda i: ys[i])
    out = list(ys)
    prev = lo - gap
    for i in order:
        out[i] = max(out[i], prev + gap)
        prev = out[i]
    nxt = hi + gap
    for i in reversed(order):
        out[i] = min(out[i], nxt - gap)
        nxt = out[i]
    return out


def map_svg(c: dict[str, Any]) -> str:
    spot, lv, tw = c["centre"], c["levels"], c["three_ways"]
    r50, r80 = c["ranges"]["50%"], c["ranges"]["80%"]
    late = c.get("latest") or {}
    late_p = late.get("spot") if late.get("after_book") else None
    marks = [x for x in (r80[0], r80[1], (lv.get("cap") or {}).get("strike"), (lv.get("floor") or {}).get("strike"),
                         c["switch_0dte"], c["board_switch"], c.get("board_switch_after_open"),
                         c.get("prior_close"), late_p) if x]
    top_p, bot_p = max(marks) + 15, min(marks) - 15
    W, H, T, B = 360, 620, 10, 10
    y = lambda p: T + (top_p - p) / (top_p - bot_p) * (H - T - B)  # noqa: E731
    o = [f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" aria-label="SPX price map, to scale" '
         f'xmlns="http://www.w3.org/2000/svg" font-family="Inter,system-ui,sans-serif">']
    # regime spine: rust below the 0DTE switch, amber between switches, teal above the board switch
    sw = [x for x in (c["switch_0dte"], c["board_switch"]) if x]
    s_lo, s_hi = (min(sw), max(sw)) if sw else (top_p, top_p)
    for a, b, col in ((top_p, s_hi, TEAL), (s_hi, s_lo, AMBER), (s_lo, bot_p, RUST)):
        if a > b:
            o.append(f'<rect x="56" y="{y(a):.1f}" width="8" height="{y(b) - y(a):.1f}" fill="{col}"/>')
    # air pockets: dashed boxes
    for a, b in lv.get("air_pockets", []):
        o.append(f'<rect x="68" y="{y(b):.1f}" width="172" height="{y(a) - y(b):.1f}" fill="none" '
                 f'stroke="{INK}" stroke-dasharray="4 4" stroke-width="1.2" opacity=".55"/>')
    rows: list[tuple[float, str, str, str, str]] = []   # (price, price label colour, name, detail, kind)
    cap, flo = lv.get("cap"), lv.get("floor")
    if cap:
        o.append(f'<rect x="68" y="{y(cap["strike"]) - 2:.1f}" width="172" height="4" fill="{TEAL}"/>')
        rows.append((cap["strike"], TEAL, "Cap", f'call OI {cap["call_oi"]:,}', "w"))
    if flo:
        o.append(f'<rect x="68" y="{y(flo["strike"]) - 2:.1f}" width="172" height="4" fill="{RUST}"/>')
        rows.append((flo["strike"], RUST, "Floor", f'put OI {flo["put_oi"]:,}', "w"))
    for s in lv.get("steps", []):
        k, side = s["strike"], ("teal" if s["call_oi"] > 2 * s["put_oi"] else
                                "rust" if s["put_oi"] > 2 * s["call_oi"] else "ink")
        o.append(f'<rect x="68" y="{y(k) - 3:.1f}" width="172" height="6" fill="{TINT[side]}"/>')
        rows.append((k, INK, "Step", f'{s["call_oi"] + s["put_oi"]:,} OI', "s"))
    for k, name, det in ((c["board_switch"], "Board switch", "all expiries"),
                         (c.get("board_switch_after_open"), "Board after 9:30", "AM-settled gone"),
                         (c["switch_0dte"], "0DTE switch", "today's expiry")):
        if k:
            o.append(f'<line x1="68" x2="240" y1="{y(k):.1f}" y2="{y(k):.1f}" stroke="{INK}" '
                     f'stroke-width="2" stroke-dasharray="6 4"/>')
            rows.append((k, INK, name, det, "d"))
    for k, name, col in ((c.get("prior_close"), "Prior close", "#5b6675"), (late_p, "Latest SPX", AMBER)):
        if k and abs(k - spot) > 4:
            o.append(f'<line x1="68" x2="240" y1="{y(k):.1f}" y2="{y(k):.1f}" stroke="{col}" '
                     f'stroke-width="1.5" stroke-dasharray="2 3"/>')
            rows.append((k, col, name, "after this book" if name.startswith("Latest") else "before the gap", "m"))
    o.append(f'<line x1="68" x2="240" y1="{y(spot):.1f}" y2="{y(spot):.1f}" stroke="{INK}" stroke-width="2.5"/>'
             f'<text x="236" y="{y(spot) - 5:.1f}" text-anchor="end" fill="{INK}" font-size="13" '
             f'font-weight="700">forward</text>')
    o.append(f'<rect x="0" y="{y(spot) - 11:.1f}" width="54" height="22" rx="3" fill="{INK}"/>'
             f'<text x="27" y="{y(spot) + 5:.1f}" text-anchor="middle" fill="#fff" font-size="15" '
             f'font-weight="700" font-family="JetBrains Mono,monospace">{f0(spot)}</text>')
    # name + detail sit just ABOVE their line (never struck through); rows nudged apart when levels crowd
    ly = spread_labels([y(r[0]) for r in rows], 36, T + 36, H - B)
    # left price column: also nudged apart (two levels 2 pts apart must not print on top of each other),
    # keeping clear of the dark forward tag
    priced = [r for r in rows if abs(r[0] - spot) > 6]
    py = spread_labels([y(r[0]) for r in priced] + [y(spot)], 17, T + 8, H - B - 4)
    for (p, col, *_), yy in zip(priced, py[:-1], strict=True):
        if abs(yy - py[-1]) < 17:
            yy = py[-1] + (17 if yy >= py[-1] else -17)
        o.append(f'<text x="52" y="{yy + 5:.1f}" text-anchor="end" fill="{col}" font-size="15" '
                 f'font-weight="700" font-family="JetBrains Mono,monospace">{f0(p)}</text>')
    for (p, col, name, det, _), yy in zip(rows, ly, strict=True):
        halo = 'stroke="#fbfaf7" stroke-width="4" paint-order="stroke"'    # readable where a line runs under
        o.append(f'<text x="72" y="{yy - 21:.1f}" fill="{INK}" font-size="15" font-weight="700" {halo}>{E(name)}</text>'
                 f'<text x="72" y="{yy - 5:.1f}" fill="#2b3542" font-size="14" {halo}>{E(det)}</text>')
    # odds rail on the axis: break / range / pop
    lo_e, hi_e = tw["edges"]
    for a, b, col, tint, pct, mk in ((top_p, hi_e, TEAL, TINT["teal"], tw["pop"], "▲"),
                                     (hi_e, lo_e, INK, TINT["ink"], tw["range"], "◆"),
                                     (lo_e, bot_p, RUST, TINT["rust"], tw["break"], "▼")):
        if a <= b:
            continue
        y0, y1 = y(a), y(b)
        o.append(f'<rect x="248" y="{y0 + 1:.1f}" width="30" height="{y1 - y0 - 2:.1f}" rx="4" fill="{tint}" '
                 f'stroke="{col}" stroke-width="1.2"/>')
        mid = (y0 + y1) / 2
        o.append(f'<text x="263" y="{mid - 4:.1f}" text-anchor="middle" fill="{col}" font-size="14">{mk}</text>'
                 f'<text x="263" y="{mid + 13:.1f}" text-anchor="middle" fill="{col}" font-size="13" '
                 f'font-weight="700" font-family="JetBrains Mono,monospace">{pct:.0f}%</text>')
    # brackets: outer = 80%, inner = 50% close range; their four end prices in one column on the right
    ends = []
    for (a, b), x0, wt in ((r80, 284, 2.4), (r50, 294, 1.6)):
        o.append(f'<path d="M{x0 + 8} {y(b):.1f} H{x0} V{y(a):.1f} H{x0 + 8}" fill="none" stroke="{INK}" '
                 f'stroke-width="{wt}"/>')
        ends += [b, a]
    ey = spread_labels([y(e) for e in ends], 17, T + 8, H - B - 2)
    for e, yy in zip(ends, ey, strict=True):
        o.append(f'<text x="306" y="{yy + 5:.1f}" font-size="13" fill="{INK}" '
                 f'font-family="JetBrains Mono,monospace">{f0(e)}</text>')
    o.append("</svg>")
    return "".join(o)


# ------------------------------------------------------------------ the page
def headline(c: dict[str, Any]) -> str:
    s, sw, bd, g = c["centre"], c["switch_0dte"], c["board_switch"], c["gamma"]
    parts = []
    if sw:
        parts.append(f"<b>{abs(s - sw):,.0f} pts</b> {'under' if s < sw else 'over'} the 0DTE switch ({f0(sw)})")
    if bd:
        parts.append(f"<b>{abs(s - bd):,.0f}</b> {'under' if s < bd else 'over'} the board switch ({f0(bd)})")
    out = (f"The 08:35 forward <b>{f0(s)}</b> sits " + " and ".join(parts) + "."
           if parts else f"The 08:35 forward is <b>{f0(s)}</b>.")
    a, d, u = g.get("at_centre"), g.get("down_1pct"), g.get("up_1pct")
    if a is not None and d is not None and u is not None:
        if a < 0:
            up = "to <b>about zero</b>" if abs(u / a) < 0.25 else f"to <b>{u / a:.1f}×</b>"
            if u > 0:
                up = "<b>past zero</b> (dealers turn long gamma)"
            out += f" A 1% drop makes dealers' short gamma <b>{d / a:.1f}× bigger</b>; a 1% pop takes it {up}."
        elif d < 0:
            out += " Dealers are long gamma here, but a 1% drop flips them <b>short</b> - moves would speed up."
        else:
            out += " Dealers are long gamma here: they sell rips and buy dips."
    return out


def overnight_html(c: dict[str, Any]) -> str:
    on = c.get("overnight") or {}
    rows = []
    gap = on.get("gap_pts")
    if gap is not None:
        x = on.get("gap_x_prev_move")
        rows.append(("Price gap", f"<b>{gap:+,.0f} pts</b> (prior close {f0(c['prior_close'])} → forward {f0(c['centre'])})"
                     + (f" = <b>{x:.1f}×</b> yesterday's expected move" if x is not None else "")))
    flags = {"spike": (RUST, "VOL SPIKE"), "crush": (TEAL, "VOL CRUSH"), "normal": ("#2b3542", "normal")}
    for k, name in (("7d", "Vol, 1 week"), ("30d", "Vol, 1 month")):
        v = (on.get("vol") or {}).get(k)
        if v:
            col, word = flags[v["flag"]]
            rank = f", rank {v['pct_rank']}% of {v['n']} mornings" if v.get("pct_rank") is not None else ""
            rows.append((name, f"{v['now']:.1f}% vs {v['prev']:.1f}% yesterday 08:35 = <b>{v['change']:+.1f} pts</b>"
                         f"{rank} · <b style='color:{col}'>{word}</b>"))
    sc = on.get("vol_since_close")
    if sc:
        rows.append(("Since close", f"1-month {sc['close']:.1f}% at 17:05 → {sc['now']:.1f}% now = "
                     f"<b>{sc['change']:+.1f} pts</b> <span class='sub'>(close = vendor IV, a second opinion)</span>"))
    oi = on.get("oi") or {}
    if oi.get("changed_pct") is not None:
        word = "updated overnight" if oi.get("updated") else "<b style='color:#a8461f'>NOT updated</b> - yesterday's positions"
        top = ", ".join(f"{f0(t['strike'])} {t['side']} {t['change']:+,}" for t in oi.get("top", []))
        rows.append(("Positions", f"{word} ({oi['changed_pct']:.0f}% of contracts changed)"
                     + (f". Biggest near price: {top}" if top else "")))
    if on.get("prev_straddle"):
        rows.append(("Expected move", f"today <b>±{c['straddle']:,.0f}</b> vs ±{on['prev_straddle']:,.0f} yesterday"))
    if not rows:
        return ""
    body = "".join(f'<div class="it"><span class="k" style="padding-top:3px">{E(n)}</span><div>{t}</div></div>'
                   for n, t in rows)
    return f'<div class="sec"><h2>Overnight change</h2><span class="k">08:35 vs 08:35</span></div><div class="on">{body}</div>'


def checks_html(c: dict[str, Any]) -> str:
    ck, bad = c["checks"], []
    if not ck.get("front_is_today"):
        bad.append(f"this card uses the {c['label']} for the {c['session']} expiry - the real card for that day "
                   "comes after that morning's 08:35 pull")
    if ck.get("oi_updated") is False:
        bad.append("open interest did not change overnight - these are the day-before's positions")
    if (ck.get("forward_spread_pts") or 0) > 2:
        bad.append(f"call/put prices give forwards {ck['forward_spread_pts']:.1f} pts apart")
    if c["odds_quality"] != "smile":
        bad.append("the smile could not be read - odds are a plain bell curve from the ATM IV")
    bad += c.get("odds_reasons") or []
    if not bad:
        return (f'<div class="ok">Checks passed: one book ({E(c["label"])}), centred on its own forward '
                f'(call/put prices agree within {ck.get("forward_spread_pts", 0):.2f} pts), open interest updated, '
                f'smooth odds from {ck.get("smile_points")} strikes.</div>')
    return '<div class="warn"><b>Check:</b> ' + "; ".join(E(x) for x in bad) + "</div>"


def since_morning(m: dict[str, Any], c: dict[str, Any]) -> dict[str, Any]:
    """09:45 refresh vs the 08:35 card: what moved."""
    d = lambda a, b: None if a is None or b is None else round(b - a, 1)  # noqa: E731
    return {"forward": [m["centre"], c["centre"], d(m["centre"], c["centre"])],
            "straddle": [m["straddle"], c["straddle"], d(m["straddle"], c["straddle"])],
            "switch_0dte": [m["switch_0dte"], c["switch_0dte"], d(m["switch_0dte"], c["switch_0dte"])],
            "board_switch": [m["board_switch"], c["board_switch"], d(m["board_switch"], c["board_switch"])],
            "pop": [m["three_ways"]["pop"], c["three_ways"]["pop"], d(m["three_ways"]["pop"], c["three_ways"]["pop"])],
            "break": [m["three_ways"]["break"], c["three_ways"]["break"],
                      d(m["three_ways"]["break"], c["three_ways"]["break"])]}


def since_html(c: dict[str, Any]) -> str:
    sm = c.get("since_morning")
    if not sm:
        return ""
    names = {"forward": "Price", "straddle": "Expected move ±", "switch_0dte": "0DTE switch",
             "board_switch": "Board switch", "pop": "Pop odds %", "break": "Break odds %"}
    body = "".join(f'<div class="it"><span class="k" style="padding-top:3px">{E(names[k])}</span>'
                   f'<div>{f0(a)} → <b>{f0(b)}</b>' + (f' ({dd:+,.1f})' if dd is not None else "") + "</div></div>"
                   for k, (a, b, dd) in sm.items())
    return f'<div class="sec"><h2>Since 08:35</h2><span class="k">live refresh</span></div><div class="on">{body}</div>'


def render(c: dict[str, Any]) -> str:
    sess = date.fromisoformat(c["session"])
    reg_col = {"neg": RUST, "pos": TEAL, "mid": AMBER}[c["regime"]["key"]]
    tw = c["three_ways"]
    lo, hi = tw["edges"]
    bar = "".join(f'<div style="flex:{max(v, 0.5)} 1 0;background:{col}">{v:.0f}%</div>'
                  for v, col in ((tw["break"], RUST), (tw["range"], INK), (tw["pop"], TEAL)))
    three = "".join(f'<div class="r3"><span style="color:{col}">{mk}</span><span><b>{name}</b> · {E(rng)} · '
                    f'<b>{v:.0f}%</b></span></div>'
                    for mk, col, name, rng, v in (("▼", RUST, "Break", f"closes under {f0(lo)}", tw["break"]),
                                                  ("◆", INK, "Range", f"{f0(lo)} to {f0(hi)}", tw["range"]),
                                                  ("▲", TEAL, "Pop", f"closes over {f0(hi)}", tw["pop"])))
    rows = "".join(f'<div class="it"><span style="color:{MARK[r["icon"]][1]}">{MARK[r["icon"]][0]}</span>'
                   f'<div><b>{E(r["if"])}</b><div class="sub">{E(r["then"])}</div></div></div>'
                   for r in c["if_then"])
    src = "".join(f"<li><b>{E(k.replace('_', ' '))}</b>: {E(v)}</li>" for k, v in c["sources"].items())
    late = c.get("latest") or {}
    prices = "".join(f'<div><span class="k">{E(a)}</span><b style="color:{col}">{f0(v)}</b><span class="sub">{E(b)}</span></div>'
                     for a, v, b, col in (("PRIOR CLOSE", c["prior_close"], "last print", "#2b3542"),
                                          ("08:35 FORWARD", c["centre"], "card centre", INK),
                                          ("LATEST", late.get("spot"), "after this book" if late.get("after_book") else "",
                                           AMBER if late.get("after_book") else "#2b3542")) if v is not None)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>SPX 0DTE map · {sess:%a %b %d}</title>
<style>
body{{margin:0;background:#fbfaf7;color:{INK};font:16px/1.45 Inter,system-ui,sans-serif}}
.c{{max-width:520px;margin:0 auto;padding:16px;display:flex;flex-direction:column;gap:16px}}
h1{{margin:0;font-size:27px;line-height:1.15}} h2{{margin:0;font-size:20px}}
.k{{font:700 13px/1 'JetBrains Mono',monospace;letter-spacing:.06em;color:#3c4654}}
.pill{{padding:12px 14px;border-radius:8px;color:#fff;font-weight:700;font-size:15px;background:{reg_col}}}
.sec{{border-top:2px solid {INK};padding-top:8px;display:flex;justify-content:space-between;align-items:baseline}}
.bar{{display:flex;height:46px;border-radius:6px;overflow:hidden;color:#fff;font:700 16px 'JetBrains Mono',monospace}}
.bar div{{display:flex;align-items:center;justify-content:center;min-width:0}}
.r3,.it{{display:grid;grid-template-columns:24px minmax(0,1fr);gap:6px;font-size:16px}}
.it{{padding:9px 0;border-bottom:1px solid #c9cfd8}} .sub{{color:#2b3542;font-size:15px}}
.ok{{background:#e3f1ec;border:1px solid {TEAL};border-radius:6px;padding:9px 12px;font-size:15px}}
.warn{{background:{TINT['amber']};border:1px solid {AMBER};border-radius:6px;padding:9px 12px;font-size:15px}}
.src{{font-size:14px;color:#2b3542;margin:0;padding-left:18px}} .src li{{margin-bottom:4px}}
.leg{{display:flex;flex-wrap:wrap;gap:6px 14px;font-weight:600;font-size:14px}}
.leg i{{display:inline-block;width:12px;height:12px;border-radius:2px;margin-right:6px;vertical-align:-1px}}
.px{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px}} .px div{{background:#fff;border:1px solid #c9cfd8;border-radius:6px;padding:7px 8px;display:flex;flex-direction:column;gap:2px}}
.px b{{font:700 20px 'JetBrains Mono',monospace}} .on .it{{grid-template-columns:96px minmax(0,1fr);font-size:15px}}
</style></head><body><div class="c">
<div><div class="k">SPX · 0DTE MAP · DESCRIPTOR, NOT A SIGNAL</div>
<h1>SPX · {sess:%a %b %d} · 0DTE</h1>
<div style="color:#3c4654;font-weight:500">From the {E(c["label"])}</div></div>
<div class="px">{prices}</div>
<div class="pill">{E(c["regime"]["line"])}</div>
<p style="margin:0;font-size:17px">{headline(c)}</p>
<div class="leg"><span><i style="background:{RUST}"></i>neg Γ · moves feed themselves</span>
<span><i style="background:{AMBER}"></i>between the switches</span><span><i style="background:{TEAL}"></i>pos Γ · moves calm</span></div>
<div class="sec"><h2>The map</h2><span class="k">price to scale</span></div>
{map_svg(c)}
<div style="font-size:14px;color:#2b3542">Middle rail = close odds on the price axis. Brackets = where the close lands
(outer 80%, inner 50% of the time, from option prices). Dashed box = gap with little open interest. Straddle ±{c["straddle"]:,.0f}
· 1σ {f0(c["one_sigma"][0])}–{f0(c["one_sigma"][1])}. Gamma assumes {E(c["dealer_sign"])}.</div>
{since_html(c)}{overnight_html(c)}
<div class="sec"><h2>Three ways it closes</h2><span class="k">not a forecast</span></div>
<div class="bar">{bar}</div><div>{three}</div>
{checks_html(c)}
<div class="sec"><h2>If / then</h2><span class="k">descriptor</span></div><div>{rows}</div>
<div class="sec"><h2>Where each number comes from</h2></div><ul class="src">{src}</ul>
<div style="font-size:14px;color:#2b3542"><b>Provisional.</b> Open interest is the book's; the card is rebuilt
on the next book. Odds are risk-neutral (from option prices), not a forecast.</div>
</div></body></html>"""


def caption(c: dict[str, Any]) -> str:
    tw = c["three_ways"]
    on = c.get("overnight") or {}
    v7 = (on.get("vol") or {}).get("7d") or {}
    spike = " · VOL SPIKE" if v7.get("flag") == "spike" else ""
    return (f"SPX {date.fromisoformat(c['session']):%a %m/%d} 0DTE map · forward {f0(c['centre'])} "
            f"(gap {on.get('gap_pts', 0):+,.0f}) · {c['regime']['line'].split(' · ')[0].lower()}{spike}\n"
            f"Close: under {f0(tw['edges'][0])} {tw['break']:.0f}% · range {tw['range']:.0f}% · "
            f"over {f0(tw['edges'][1])} {tw['pop']:.0f}% (option prices, not a forecast)")


def _live_rows(settings: Any, symbol: str) -> tuple[list[dict[str, Any]], datetime]:
    """One live chain pull (Convex, 1 call) shaped like the stored book's rows."""
    from trading_intel.clients.convex import ConvexClient
    from trading_intel.scheduler.jobs.oi_chain_eod import _chain_to_records
    from trading_intel.timeutils import eastern_now
    now = eastern_now().replace(second=0, microsecond=0)
    recs = _chain_to_records(ConvexClient(settings).chain_long(symbol), symbol=symbol, ts=now, window_days=60,
                             source="convex_live")
    return ([{"expiry": r["expiry"], "strike": r["strike"], "cp": r["cp"], "oi": r.get("oi") or 0,
              "bid": r.get("bid_price"), "ask": r.get("ask_price"), "source": r["source"], "iv": r.get("iv")}
             for r in recs], now)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--symbol", default="SPX")
    ap.add_argument("--as-of", dest="as_of", default=None)
    ap.add_argument("--live", action="store_true", help="09:45 refresh: one live chain pull, vs the 08:35 card")
    ap.add_argument("--no-telegram", action="store_true")
    a = ap.parse_args()
    from trading_intel.config import get_settings
    from trading_intel.market.odte_card import build_odte_card
    from trading_intel.memory.db import make_session_factory
    st = get_settings()
    with make_session_factory(st)() as s:
        c = build_odte_card(s, a.symbol, as_of=date.fromisoformat(a.as_of) if a.as_of else None)
        if a.live and c.get("found"):
            rows, now = _live_rows(st, a.symbol)
            live = build_odte_card(s, a.symbol, live_rows=rows, live_dt=now)
            if live.get("found"):
                live["since_morning"] = since_morning(c, live)
                c = live
            else:
                print("odte_card.live_failed", live.get("reason"))
                sys.exit(1)
    if not c.get("found"):
        print("odte_card.not_found", c.get("reason"))
        sys.exit(1)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stem = OUT_DIR / f"odte_card_{c['session']}_{datetime.now():%H%M}{'_live' if a.live else ''}"
    page = stem.with_suffix(".html")
    page.write_text(render(c), encoding="utf-8")
    png = stem.with_suffix(".png")
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            b = pw.chromium.launch()
            pg = b.new_page(viewport={"width": 390, "height": 900}, java_script_enabled=False)
            pg.goto(page.as_uri())
            pg.screenshot(path=str(png), full_page=True)
            b.close()
    except Exception as exc:  # noqa: BLE001 - the HTML still goes out
        print("odte_card.png_failed", exc)
        png = None
    cap = ("09:45 REFRESH · " if a.live else "") + caption(c)
    print("odte_card.done", page, "|", cap.replace("\n", " | "))
    if not a.no_telegram:
        from trading_intel.clients.telegram import TelegramClient
        tg = TelegramClient(st)
        if png is not None:
            print("telegram_photo=", tg.send_photo(png, caption=cap))
        print("telegram_doc=", tg.send_document(page, caption="SPX 0DTE map card (full page)"))


if __name__ == "__main__":
    main()
