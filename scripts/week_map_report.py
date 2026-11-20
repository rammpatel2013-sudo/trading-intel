#!/usr/bin/env python
# ruff: noqa: E501, N806, RUF001, RUF100
"""week_map_report.py - created 2026-11-20 for Mithil. SPX WEEK map + MONTH map as phone pages (Telegram).

VolSignals-style "here is the map now" picture from our 08:35 SPX book (market.week_map): the Line, the
dealer-long bands, gamma flip + vanna flip day by day, decay zones (dealers buy / sell), the 1-sigma cone,
the 09:30 AM-settlement marker, and three illustrated paths (vol down / unchanged / up). Re-built every
morning, so the week map shrinks one day at a time ("update as days pass"). Plus yesterday's score: where
each path said the close would be vs the real close. Every number and the chart (inline SVG) are written
at build time - no scripts, so Telegram's phone preview shows all of it.

    python scripts/week_map_report.py                    # both maps from the newest book -> Telegram
    python scripts/week_map_report.py --which week --as-of 2026-11-19 --no-telegram
NAS (DSM Mon-Fri 08:50, after the 08:35 book):
    sudo bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_script.sh week_map_report
Descriptor, not a signal (rule 4). Paths are illustrations, not forecasts.
"""
from __future__ import annotations

import argparse
import html
import sys
from datetime import date
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

OUT_DIR = REPO / "reports" / "week_map"
E = html.escape
INK, RED, GREEN, BLUE, PURPLE, GOLD, GREY = "#111827", "#c0392b", "#1e8449", "#1f6feb", "#7d3cc8", "#b7950b", "#6b7280"
BUY_BG, SELL_BG, LONG_BG = "#dff3e6", "#f8dcdc", "#f7e7a1"
PATH = {"down": (GREEN, "VOL DOWN"), "flat": (INK, "VOL UNCHANGED"), "up": (RED, "VOL UP")}


def f0(x: float | None) -> str:
    return "–" if x is None else f"{x:,.0f}"


# ------------------------------------------------------------------ chart
def svg_map(m: dict[str, Any], w: int = 620, h: int = 820) -> str:
    L, R, T, B = 78, 36, 40, 66
    days = m["days"]
    n = len(days)
    xs = [L + (w - L - R) * i / n for i in range(n + 1)]          # x[0] = book time, x[i] = day i close
    lv = m["levels"]
    vals = [v for p in m["paths"].values() for v in p] + [c for d in days for c in d["cone"]]
    vals += [d[k] for d in days for k in ("gamma_flip", "vanna_flip") if d.get(k)]
    vals += [x["strike"] for x in lv.values() if x]
    lo, hi = min(vals), max(vals)
    pad = 0.06 * (hi - lo)
    lo, hi = lo - pad, hi + pad

    def y(p: float) -> float:
        return T + (h - T - B) * (hi - p) / (hi - lo)

    o = [f'<svg viewBox="0 0 {w} {h}" width="100%" xmlns="http://www.w3.org/2000/svg" font-family="Segoe UI,Arial,sans-serif">',
         f'<rect x="{L}" y="{T}" width="{w-L-R}" height="{h-T-B}" fill="#fafafa" stroke="{INK}" stroke-width="2"/>']
    # decay zones per day column
    for i, d in enumerate(days):
        for z in d["decay_zones"]:
            if z["side"] == 0 or z["hi"] < lo or z["lo"] > hi:
                continue
            y1, y2 = y(min(z["hi"], hi)), y(max(z["lo"], lo))
            o.append(f'<rect x="{xs[i]:.1f}" y="{y1:.1f}" width="{xs[i+1]-xs[i]:.1f}" height="{max(y2-y1, 1):.1f}" '
                     f'fill="{BUY_BG if z["side"] > 0 else SELL_BG}"/>')
    # price grid
    step = next(s for s in (25, 50, 100, 200, 250, 500) if (hi - lo) / s <= 8)
    p = (int(lo / step) + 1) * step
    while p < hi:
        o.append(f'<line x1="{L}" x2="{w-R}" y1="{y(p):.1f}" y2="{y(p):.1f}" stroke="#d1d5db" stroke-width="1"/>'
                 f'<text x="{L-8}" y="{y(p)+7:.1f}" font-size="22" text-anchor="end" fill="{INK}">{p:,.0f}</text>')
        p += step
    # dealer-long bands + the Line
    band = max(0.0012 * m["forward"], 4.0)
    for key in ("longs_above", "longs_below"):
        if lv.get(key):
            k = lv[key]["strike"]
            o.append(f'<rect x="{L}" y="{y(k+band):.1f}" width="{w-L-R}" height="{y(k-band)-y(k+band):.1f}" fill="{LONG_BG}" opacity="0.9"/>'
                     f'<text x="{L+8}" y="{y(k+band)-5:.1f}" font-size="22" font-weight="800" fill="{GOLD}">MM LONGS {k:,.0f}</text>')
    if lv.get("line"):
        k = lv["line"]["strike"]
        o.append(f'<line x1="{L}" x2="{w-R}" y1="{y(k):.1f}" y2="{y(k):.1f}" stroke="{RED}" stroke-width="4"/>'
                 f'<text x="{w-R-6}" y="{y(k)+26:.1f}" font-size="22" font-weight="800" text-anchor="end" fill="{RED}">THE LINE {k:,.0f}</text>')
    # cone, flips
    for j, dash in ((0, "6,6"), (1, "6,6")):
        pts = " ".join(f"{xs[i+1]:.1f},{y(d['cone'][j]):.1f}" for i, d in enumerate(days))
        o.append(f'<polyline points="{xs[0]:.1f},{y(m["forward"]):.1f} {pts}" fill="none" stroke="{GREY}" stroke-width="2" stroke-dasharray="{dash}"/>')
    for key, col in (("gamma_flip", BLUE), ("vanna_flip", PURPLE)):
        pts = [(xs[i + 1], d[key]) for i, d in enumerate(days) if d.get(key) and lo < d[key] < hi]
        if len(pts) >= 2:
            o.append(f'<polyline points="{" ".join(f"{a:.1f},{y(b):.1f}" for a, b in pts)}" fill="none" stroke="{col}" stroke-width="3" stroke-dasharray="10,6"/>')
    # AM settlement
    if m.get("am_settle"):
        x = xs[-1] - 0.35 * (xs[-1] - xs[-2])
        o.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{T}" y2="{h-B}" stroke="{INK}" stroke-width="2" stroke-dasharray="4,5"/>'
                 f'<text x="{x-6:.1f}" y="{T+24}" font-size="20" font-weight="700" text-anchor="end" fill="{INK}">AM 9:30</text>')
    # paths
    for name, (col, _lab) in PATH.items():
        ps = m["paths"][name]
        o.append(f'<polyline points="{" ".join(f"{xs[i]:.1f},{y(v):.1f}" for i, v in enumerate(ps))}" fill="none" stroke="{col}" stroke-width="5"/>')
        o.extend(f'<circle cx="{xs[i]:.1f}" cy="{y(v):.1f}" r="7" fill="#fff" stroke="{col}" stroke-width="4"/>'
                 for i, v in enumerate(ps) if n <= 7 or i in (0, n))
        o.append(f'<text x="{xs[-1]-14:.1f}" y="{y(ps[-1])+(30 if name == "up" else -14):.1f}" font-size="24" font-weight="800" text-anchor="end" fill="{col}">{f0(ps[-1])}</text>')
    o.append(f'<circle cx="{xs[0]:.1f}" cy="{y(m["forward"]):.1f}" r="9" fill="{INK}"/>')
    # x labels
    o.append(f'<text x="{xs[0]:.1f}" y="{h-B+30}" font-size="21" text-anchor="middle" fill="{INK}">now</text>')
    every = max(1, n // 5)
    for i, d in enumerate(days):
        if (i % every == 0 and (n <= 7 or i > 0) and n - 1 - i >= every / 2) or i == n - 1:
            o.append(f'<text x="{xs[i+1]:.1f}" y="{h-B+30}" font-size="21" font-weight="700" text-anchor="middle" fill="{INK}">{E(d["label"][:3])}</text>'
                     f'<text x="{xs[i+1]:.1f}" y="{h-B+54}" font-size="20" text-anchor="middle" fill="{INK}">{E(d["label"][4:])}</text>')
    o.append("</svg>")
    return "".join(o)


# ------------------------------------------------------------------ page
def if_then(m: dict[str, Any]) -> list[str]:
    tgt = date.fromisoformat(m["target"]).strftime("%a %m/%d")
    lv = m["levels"]
    out = []
    step = f" ({m['vol_step_pts']:g} pts a day, {m['vol_cap_pts']:g} max)"
    for name, (_, lab) in PATH.items():
        out.append(f"If {lab.lower()}{'' if name == 'flat' else step} → about <b>{f0(m['ends'][name])}</b> by {tgt}")
    if lv.get("line"):
        k = lv["line"]["strike"]
        side = "ABOVE" if m["forward"] > k else "BELOW"
        out.append(f"Price is <b>{side} the Line ({k:,.0f})</b> — the strike where dealers are shortest gamma: "
                   f"moves through it can speed up.")
    return out


def score_line(sc: dict[str, Any] | None) -> str:
    if not sc:
        return ""
    md = sc["modeled"]
    return (f'<div class="box"><b>Yesterday ({E(sc["day"])}) scored:</b> close <b>{f0(sc["close"])}</b> · '
            f'vol down {f0(md["down"])} · unchanged {f0(md["flat"])} · vol up {f0(md["up"])} → nearest '
            f'<b>{E(PATH[sc["nearest"]][1].lower())}</b> (miss {sc["miss_pts"][sc["nearest"]]:+,.0f} pts)</div>')


def render(m: dict[str, Any], score: dict[str, Any] | None = None) -> str:
    title = "WEEK MAP" if m["kind"] == "week" else "MONTH MAP"
    tgt = date.fromisoformat(m["target"]).strftime("%a %b %d")
    lv = m["levels"]
    d0 = m["days"][0]
    rows = "".join(
        f"<tr><td>{E(d['label'])}</td><td>{f0(d['gamma_flip'])}</td><td>{f0(d['vanna_flip'])}</td>"
        f"<td>{f0(d['cone'][0])}–{f0(d['cone'][1])}</td><td class='{'g' if d['decay_at_flat_musd'] > 0 else 'r'}'>"
        f"{'BUY' if d['decay_at_flat_musd'] > 0 else 'SELL'} {abs(d['decay_at_flat_musd'])/1000:,.1f}bn</td></tr>"
        for d in m["days"])
    lines = "".join(f"<li>{x}</li>" for x in if_then(m))
    lvl = " · ".join(f"{lab} <b>{f0((lv.get(k) or {}).get('strike'))}</b>" for k, lab in
                     (("line", "Line (largest MM short)"), ("longs_above", "MM longs above"), ("longs_below", "MM longs below")))
    src = "".join(f"<li><b>{E(k)}</b>: {E(v)}</li>" for k, v in m["sources"].items())
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SPX {title} to {tgt}</title><style>
body{{margin:0;background:#fff;color:{INK};font:20px/1.45 'Segoe UI',Arial,sans-serif}}
.wrap{{max-width:640px;margin:0 auto;padding:12px}} h1{{font-size:28px;margin:4px 0}} .sub{{font-size:18px;margin-bottom:8px}}
ul{{padding-left:22px;margin:8px 0}} li{{margin:6px 0}} table{{border-collapse:collapse;width:100%;font-size:18px}}
td,th{{border-bottom:1px solid #cbd5e1;padding:6px 4px;text-align:right}} td:first-child,th:first-child{{text-align:left}}
.g{{color:{GREEN};font-weight:700}} .r{{color:{RED};font-weight:700}} .box{{background:#eef2ff;padding:10px;border-radius:8px;margin:10px 0}}
.key span{{display:inline-block;margin:3px 10px 3px 0;font-weight:700}}
</style></head><body><div class="wrap">
<h1>SPX {title} → {E(tgt)}</h1>
<div class="sub">From the {E(m['book_time'].replace('T', ' '))} book · forward <b>{f0(m['forward'])}</b> · ATM IV <b>{100*m['atm_iv']:.1f}%</b> · {E(m['dealer_sign'])}</div>
{svg_map(m)}
<div class="key"><span style="color:{GREEN}">— vol down</span><span>— vol unchanged</span><span style="color:{RED}">— vol up</span>
<span style="color:{BLUE}">- - gamma flip</span><span style="color:{PURPLE}">- - vanna flip</span><span style="color:{GREY}">- - 1σ cone</span>
<span style="background:{BUY_BG}">decay = dealers BUY</span> <span style="background:{SELL_BG}">decay = dealers SELL</span></div>
<ul>{lines}</ul>
<div class="box">{lvl}{f" · AM settlement <b>{E(m['am_settle'])}</b>" if m.get('am_settle') else ''}</div>
{score_line(score)}
<table><tr><th>Day</th><th>Gamma flip</th><th>Vanna flip</th><th>1σ range</th><th>Decay flow at price</th></tr>{rows}</table>
<p>What the numbers mean: dealers are long gamma above the gamma flip (they sell rallies, buy dips = calmer) and
short gamma below it (they chase = faster moves). Decay flow = what dealers must trade that day just from time passing,
at today's price ({'BUY' if d0['decay_at_flat_musd'] > 0 else 'SELL'} today). Paths: each day vol moves, dealers re-hedge
the change (vanna); the price goes that way until their book is balanced again or one day's 1σ.</p>
<ul style="font-size:17px">{src}</ul>
<p style="font-size:17px">{E(m['note'])}</p></div></body></html>"""


def caption(m: dict[str, Any]) -> str:
    title = "Week map" if m["kind"] == "week" else "Month map"
    e = m["ends"]
    ln = (m["levels"].get("line") or {}).get("strike")
    return (f"SPX {title} → {date.fromisoformat(m['target']):%a %m/%d} · forward {f0(m['forward'])} · Line {f0(ln)}\n"
            f"Vol down → {f0(e['down'])} · unchanged → {f0(e['flat'])} · vol up → {f0(e['up'])} (illustrations, not forecasts)")


def _png(page: Path) -> Path | None:
    png = page.with_suffix(".png")
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            b = pw.chromium.launch()
            pg = b.new_page(viewport={"width": 430, "height": 900}, device_scale_factor=2, java_script_enabled=False)
            pg.goto(page.as_uri())
            pg.screenshot(path=str(png), full_page=True)
            b.close()
        return png
    except Exception as exc:  # noqa: BLE001 - the HTML still goes out
        print("week_map.png_failed", exc)
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--which", choices=("week", "month", "both"), default="both")
    ap.add_argument("--as-of", dest="as_of", default=None)
    ap.add_argument("--no-telegram", action="store_true")
    a = ap.parse_args()
    from trading_intel.config import get_settings
    from trading_intel.market import week_map as wm
    from trading_intel.memory.db import make_session_factory
    from trading_intel.tape_sessions import next_trading_day
    from trading_intel.timeutils import eastern_now
    st = get_settings()
    as_of = date.fromisoformat(a.as_of) if a.as_of else None
    maps, score = [], None
    with make_session_factory(st)() as s:
        for which in (("week", "month") if a.which == "both" else (a.which,)):
            m = wm.build_from_db(s, which, as_of=as_of)
            if not m.get("found"):
                print(f"week_map.{which}.not_found", m.get("reason"))
                continue
            maps.append(m)
        if maps:
            bd = date.fromisoformat(maps[0]["book_day"])
            prev = bd
            for _ in range(7):                     # the trading day before the book's day
                prev = date.fromordinal(prev.toordinal() - 1)
                if next_trading_day(prev) == bd:
                    break
            try:
                score = wm.score_day(s, prev)
            except Exception as exc:  # noqa: BLE001 - the maps still go out
                print("week_map.score_failed", exc)
    if not maps:
        sys.exit(1)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tg = None
    if not a.no_telegram:
        from trading_intel.clients.telegram import TelegramClient
        tg = TelegramClient(st)
    for m in maps:
        page = OUT_DIR / f"{m['kind']}_map_{m['book_day']}_{eastern_now():%H%M}.html"
        page.write_text(render(m, score if m["kind"] == "week" else None), encoding="utf-8")
        cap = caption(m)
        print("week_map.done", page, "|", cap.replace("\n", " | "))
        if tg is not None:
            png = _png(page)
            if png is not None:
                print("telegram_photo=", tg.send_photo(png, caption=cap))
            print("telegram_doc=", tg.send_document(page, caption=f"SPX {m['kind']} map (full page)"))


if __name__ == "__main__":
    main()
