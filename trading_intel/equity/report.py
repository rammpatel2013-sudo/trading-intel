"""Daily equity / swing report v2 - phone first, picture first (2026-11-14). Pure HTML builder.

Shown on the cockpit server at :8899/equity.html (no Telegram - [stated] 2026-11-14 12:49). Built so it
reads with scripts off: every number, colour and chart is written in here (inline SVG, text >= 15 px),
taps open ``<details>`` cards (no JS). One column on a phone, two/three columns on a desktop; the picture
carries the meaning, the words are short; light + dark follow the device setting.
"""
from __future__ import annotations

import html
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from trading_intel.equity.trade_card import HEDGE_MENU, HEDGE_RULE

W = 360  # svg width = phone width, so svg text sizes are real pixels
GOOD, MID, BAD = "var(--good)", "var(--mid)", "var(--bad)"
GATE_TXT = {"green": "Conditions support buying stocks",
            "amber": "Mixed - only the strongest setups, smaller size",
            "red": "Damage is spreading - watch, don't buy"}
CSS = """
:root{--bg:#0f1216;--card:#171d25;--ink:#f2f4f7;--soft:#c9d2dc;--line:#2c3542;--good:#2fbf71;--mid:#e0a526;
--bad:#ff5d5d;--blue:#5aa9e6;--tint-good:#173b2a;--tint-mid:#3d3218;--tint-bad:#45201f;--tint-0:#232b36}
@media (prefers-color-scheme:light){:root{--bg:#f6f7f9;--card:#fff;--ink:#11161c;--soft:#3a4552;--line:#d5dbe2;
--good:#16924f;--mid:#b77a00;--bad:#d63333;--blue:#1f6fb2;--tint-good:#dff3e7;--tint-mid:#fbefd2;--tint-bad:#fbe0df;--tint-0:#eceff3}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:18px/1.4 -apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:440px;margin:0 auto;padding:14px}h1{font-size:24px;margin:4px 0 12px}h2{font-size:21px;margin:26px 0 8px}
.gate{border-radius:16px;padding:16px;color:#0f1216}.gate b{font-size:40px;letter-spacing:1px;display:block}
.gate span{font-size:18px;font-weight:600}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin-top:10px}
details{background:var(--card);border-radius:12px;border:1px solid var(--line)}summary{list-style:none;cursor:pointer;padding:10px 12px}
summary::-webkit-details-marker{display:none}.tile summary{border-radius:12px}.tile .k{font-size:15px;color:var(--soft)}
.tile .v{font-size:22px;font-weight:700}details p{margin:0;padding:0 12px 12px;font-size:16px;color:var(--soft)}
.note{font-size:16px;color:var(--soft);margin:4px 0 8px}.card{margin:10px 0}.card summary{padding:10px 12px 6px}
.t{font-size:24px;font-weight:800}.chip{display:inline-block;font-size:15px;padding:2px 8px;border-radius:99px;background:var(--tint-0);
margin:2px 4px 2px 0;color:var(--ink)}.row{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
.up{color:var(--good)}.dn{color:var(--bad)}svg{display:block;width:100%;height:auto}
@media (min-width:900px){main{max-width:1240px;padding:22px}.grid{grid-template-columns:repeat(4,minmax(0,1fr))}
.two{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:28px}
.cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.cards .card{margin:0}}
"""


def _e(x: object) -> str:
    return html.escape("" if x is None else str(x))


def _pct(x: float | None, d: int = 1) -> str:
    return "-" if x is None else f"{x:+.{d}f}%"


def _scale(vals: Sequence[float], lo_pad: float = 0.0) -> tuple[float, float]:
    lo, hi = min(vals), max(vals)
    if hi <= lo:
        hi = lo + 1
    pad = (hi - lo) * 0.08
    return lo - pad - lo_pad, hi + pad


# ── gate ────────────────────────────────────────────────────────────────
TILES = (  # vote key, label, input key, formatter
    ("damage", "Typical stock", "median_drawdown", lambda v: f"{v:.1f}% off high"),
    ("repair", "Healing (20d)", "repair_flow", lambda v: f"{v:+.0f}"),
    ("nh_nl", "New highs - lows", "nhnl_10d", lambda v: f"{v:+.1f}%"),
    ("above_50", "Above 50-day", "pct_above_50", lambda v: f"{v:.0f}%"),
    ("above_200", "Above 200-day", "pct_above_200", lambda v: f"{v:.0f}%"),
    ("index", "SPY trend", "spy_above_200", None),
    ("stockbee", "Stockbee Q ratio", "q25_ratio", lambda v: f"{v:.2f}"),
    ("beta_reset", "Beta reset", "beta_reset_label", None),
)
TILE_HELP = {
    "damage": "Median stock vs its own 1-year high. +1 if within 10%, -1 if more than 20% below.",
    "repair": "Share of stocks moving to a healthier drawdown bucket minus share moving worse, over 20 "
              "sessions. +1 above +5, -1 below -5.",
    "nh_nl": "NYSE + Nasdaq new highs minus new lows, 10-day average, % of issues (thetrading.tools). "
             "+1 above +1%, -1 below -1%.",
    "above_50": "Share of S&P 1500 stocks above their 50-day line. +1 above 60%, -1 below 40%.",
    "above_200": "Share above their 200-day line. +1 above 60%, -1 below 40%.",
    "index": "SPY above both its 50- and 200-day lines = +1; below the 200-day = -1.",
    "stockbee": "Stocks up 25% in a quarter / stocks down 25% (Stockbee's tie-breaker). +1 above 1.5, -1 below 0.67.",
    "beta_reset": "Your beta-reset rule: RESET (low beta broke, high beta held) = +1.",
}


def gate_block(g: Mapping[str, Any]) -> str:
    col = str(g.get("color") or "amber")
    votes = {k: v for k, v in (g.get("votes") or {}).items() if not k.startswith("_")}
    inp = (g.get("votes") or {}).get("_inputs", {})
    trend = g.get("trend_note")
    out = [f'<div class="gate" style="background:var(--{ {"green": "good", "red": "bad"}.get(col, "mid") })">'
           f"<b>{_e(col.upper())}</b><span>{_e(GATE_TXT.get(col, ''))}</span><br>"
           f"<span>{g.get('score', 0):+d} from {len(votes)} votes{' · ' + _e(trend) if trend else ''}</span></div>",
           '<div class="grid">']
    for key, label, ik, fmt in TILES:
        v = votes.get(key)
        tint = "tint-0" if v is None or v == 0 else ("tint-good" if v > 0 else "tint-bad")
        val = inp.get(ik)
        if key == "index":
            txt = "-" if val is None else ("above 200d" if val else "below 200d") + (
                ", above 50d" if inp.get("spy_above_50") else ", below 50d")
        elif key == "beta_reset":
            txt = str(val or "no reading")[:22]
        else:
            txt = fmt(val) if (fmt and isinstance(val, (int, float))) else "no data"
        mark = "" if v is None else ("▲ +1" if v > 0 else ("▼ -1" if v < 0 else "● 0"))
        out.append(f'<details class="tile" style="background:var(--{tint})"><summary><div class="k">{_e(label)} '
                   f'<b>{mark}</b></div><div class="v">{_e(txt)}</div></summary><p>{_e(TILE_HELP[key])}</p></details>')
    out.append("</div>")
    return "".join(out)


def ribbon_svg(hist: Sequence[Mapping[str, Any]], n: int = 60) -> str:
    rows = [r for r in hist if r.get("color")][-n:]
    if len(rows) < 2:
        return ""
    w = W / len(rows)
    rects = "".join(f'<rect x="{i * w:.1f}" y="0" width="{max(w - 1, 1):.1f}" height="34" rx="2" '
                    f'fill="var(--{ {"green": "good", "red": "bad"}.get(r["color"], "mid") })"><title>{_e(r["date"])}: '
                    f'{_e(r["color"])}</title></rect>' for i, r in enumerate(rows))
    return (f'<svg viewBox="0 0 {W} 60" role="img"><title>gate colour, last {len(rows)} sessions</title>{rects}'
            f'<text x="0" y="56" font-size="15" fill="var(--soft)">{_e(_short(rows[0]["date"]))}</text>'
            f'<text x="{W}" y="56" font-size="15" fill="var(--soft)" text-anchor="end">today</text></svg>')


def _short(d: object) -> str:
    return d.strftime("%b %d") if isinstance(d, date) else str(d)[5:10]


# ── damage pictures ─────────────────────────────────────────────────────
BUCKETS = (("<10%", GOOD), ("10-20%", "var(--blue)"), ("20-40%", MID), (">40%", BAD))


def buckets_svg(now: Sequence[float], then: Sequence[float]) -> str:
    def bar(y: int, vals: Sequence[float], label: str) -> str:
        x, parts = 0.0, [f'<text x="0" y="{y - 6}" font-size="16" fill="var(--soft)">{label}</text>']
        for (name, col), v in zip(BUCKETS, vals, strict=False):
            w = W * (v or 0) / 100
            parts.append(f'<rect x="{x:.1f}" y="{y}" width="{w:.1f}" height="34" fill="{col}"><title>{name} below high: '
                         f'{v:.0f}%</title></rect>')
            if w > 34:
                parts.append(f'<text x="{x + w / 2:.1f}" y="{y + 23}" font-size="16" font-weight="700" fill="#0f1216" '
                             f'text-anchor="middle">{v:.0f}%</text>')
            x += w
        return "".join(parts)

    legend = "".join(f'<rect x="{i * 90}" y="128" width="14" height="14" fill="{c}"/><text x="{i * 90 + 19}" y="140" '
                     f'font-size="15" fill="var(--soft)">{n}</text>' for i, (n, c) in enumerate(BUCKETS))
    return (f'<svg viewBox="0 0 {W} 148" role="img"><title>where stocks sit vs their 1-year high</title>'
            f'{bar(22, now, "Today")}{bar(84, then, "20 sessions ago")}{legend}</svg>')


def line_svg(dates: Sequence[date], vals: Sequence[float | None], *, title: str, unit: str = "%",
             bars: bool = False, h: int = 150) -> str:
    pts = [(i, v) for i, v in enumerate(vals) if v is not None]
    if len(pts) < 2:
        return ""
    lo, hi = _scale([v for _, v in pts] + [0.0])
    n = max(len(vals) - 1, 1)
    X = lambda i: W * i / n  # noqa: E731, N806
    Y = lambda v: 8 + (h - 36) * (hi - v) / (hi - lo)  # noqa: E731, N806
    body = f'<line x1="0" x2="{W}" y1="{Y(0):.1f}" y2="{Y(0):.1f}" stroke="var(--line)" stroke-width="2"/>'
    if bars:
        bw = max(W / len(vals) - 0.5, 1)
        body += "".join(f'<rect x="{X(i) - bw / 2:.1f}" y="{min(Y(v), Y(0)):.1f}" width="{bw:.1f}" '
                        f'height="{abs(Y(v) - Y(0)):.1f}" fill="{GOOD if v >= 0 else BAD}"/>' for i, v in pts)
    else:
        body += (f'<polyline fill="none" stroke="var(--blue)" stroke-width="3" '
                 f'points="{" ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in pts)}"/>')
    last = pts[-1][1]
    return (f'<svg viewBox="0 0 {W} {h}" role="img"><title>{_e(title)}</title>{body}'
            f'<text x="{W}" y="{h - 6}" font-size="16" font-weight="700" fill="var(--ink)" text-anchor="end">'
            f'now {last:+.1f}{unit}</text><text x="0" y="{h - 6}" font-size="15" fill="var(--soft)">'
            f'{_e(_short(dates[0]))}</text></svg>')


def hbars_svg(items: Sequence[tuple[str, float]], *, title: str, unit: str = "%") -> str:
    if not items:
        return ""
    h = 34 * len(items) + 6
    m = max(abs(v) for _, v in items) or 1.0
    lab, zero, half = 100, 196, 88  # labels | bars | values never overlap at 360 px
    parts = [f'<svg viewBox="0 0 {W} {h}" role="img"><title>{_e(title)}</title>'
             f'<line x1="{zero}" x2="{zero}" y1="0" y2="{h}" stroke="var(--line)" stroke-width="2"/>']
    for k, (name, v) in enumerate(items):
        y = 4 + 34 * k
        bw = half * abs(v) / m
        x = zero if v >= 0 else zero - bw
        col = GOOD if v >= 0 else BAD
        parts.append(f'<text x="{lab}" y="{y + 21}" font-size="17" fill="var(--ink)" text-anchor="end">{_e(name)}</text>'
                     f'<rect x="{x:.1f}" y="{y + 4}" width="{max(bw, 2):.1f}" height="24" rx="4" fill="{col}">'
                     f'<title>{_e(name)} {v:+.2f}{unit}</title></rect>'
                     f'<text x="{W}" y="{y + 21}" font-size="16" font-weight="700" fill="{col}" text-anchor="end">'
                     f'{v:+.1f}{unit}</text>')
    return "".join(parts) + "</svg>"


# ── candidate cards ─────────────────────────────────────────────────────
def spark_svg(closes: Sequence[float], *, stop: float | None = None, box: tuple[float, float, int] | None = None,
              h: int = 110) -> str:
    c = [x for x in closes if x is not None][-126:]
    if len(c) < 10:
        return ""
    ma = [sum(c[max(0, i - 49):i + 1]) / len(c[max(0, i - 49):i + 1]) for i in range(len(c))]
    vals = c + ma + ([stop] if stop else []) + ([box[0], box[1]] if box else [])
    lo, hi = _scale(vals)
    n = len(c) - 1
    X = lambda i: W * i / n  # noqa: E731, N806
    Y = lambda v: 4 + (h - 8) * (hi - v) / (hi - lo)  # noqa: E731, N806
    out = [f'<svg viewBox="0 0 {W} {h}" role="img"><title>last 6 months</title>']
    if box:
        x0 = X(max(0, n - box[2] + 1))
        out.append(f'<rect x="{x0:.1f}" y="{Y(box[1]):.1f}" width="{W - x0:.1f}" height="{Y(box[0]) - Y(box[1]):.1f}" '
                   f'fill="var(--mid)" opacity="0.22"><title>box {box[2]} sessions</title></rect>')
    out.append(f'<polyline fill="none" stroke="var(--soft)" stroke-width="2" stroke-dasharray="5 4" '
               f'points="{" ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(ma))}"/>')
    out.append(f'<polyline fill="none" stroke="var(--blue)" stroke-width="3" '
               f'points="{" ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(c))}"/>')
    if stop:
        out.append(f'<line x1="0" x2="{W}" y1="{Y(stop):.1f}" y2="{Y(stop):.1f}" stroke="var(--bad)" stroke-width="2" '
                   f'stroke-dasharray="8 5"><title>stop {stop:.2f}</title></line>')
    return "".join(out) + "</svg>"


def card(r: Mapping[str, Any], c: Mapping[str, Any], closes: Sequence[float] | None) -> str:
    stop = (c.get("stops") or {}).get("stop")
    sz = c.get("size") or {}
    sp = c.get("spread")
    bx = None
    if r.get("box_days") and r.get("box_high"):
        bx = (r["box_high"] / (1 + (r.get("box_width") or 0)), r["box_high"], int(r["box_days"]))
    chips = [f'<span class="chip">{_e(r.get("sector") or "-")}</span>']
    if r.get("own_move_6m") is not None:
        o = r["own_move_6m"] * 100
        chips.append(f'<span class="chip {"up" if o >= 0 else "dn"}">own {o:+.0f}% / 6m</span>')
    if bx:
        chips.append(f'<span class="chip">box {bx[2]}d · {r.get("box_width", 0) * 100:.0f}% wide</span>')
    cb = c.get("call_bid")
    if cb and r.get("list_name") == "coiled":
        chips.append('<span class="chip">call bid: ' + ("YES" if cb.get("call_bid") else
                     ("no" if cb.get("ready") else "building")) + "</span>")
    detail = []
    if stop and sz:
        detail.append(f"Stop {stop:.2f} ({sz.get('stop_distance_pct')}% away) → {sz.get('shares')} shares = "
                      f"${sz.get('position_usd'):,.0f}, risking ${sz.get('risk_usd'):,.0f} (1% of $50k).")
    if sp:
        detail.append(f"Option idea: {sp['expiry']:%b %d} {sp['long_strike']:g}/{sp['short_strike']:g} call spread, "
                      f"debit {sp['debit']:.2f} → lose at most ${sp['max_loss']:,.0f}, make up to ${sp['max_gain']:,.0f}; "
                      f"breakeven {sp['breakeven']:.2f}; model price, check the live quote.")
    if r.get("total_move_12m") is not None:
        detail.append(f"Total move 12-1 months: {_pct(r['total_move_12m'] * 100, 0)}.")
    return (f'<details class="card"><summary><div class="row"><span class="t">{_e(r["symbol"])}</span>'
            f'<span class="t" style="font-size:20px">{r.get("close", 0):.2f}</span></div>{"".join(chips)}'
            f'{spark_svg(closes or [], stop=stop, box=bx)}</summary><p>{"<br>".join(_e(x) for x in detail)}</p></details>')


def section(title: str, note: str, rows: Sequence[Mapping], data: Mapping[str, Any], n: int) -> str:
    out = [f"<h2>{_e(title)}</h2><p class='note'>{_e(note)}</p>"]
    if not rows:
        return "".join(out) + "<p class='note'>None today.</p>"
    cards, series = data.get("cards") or {}, data.get("series") or {}
    out.append('<div class="cards">')
    out += [card(r, cards.get(r["symbol"], {}), series.get(r["symbol"])) for r in rows[:n]]
    return "".join(out) + "</div>"


def build_html(data: Mapping[str, Any]) -> str:
    g = data.get("gate") or {}
    day = data.get("day")
    rows = data.get("candidates") or []
    lead = [r for r in rows if r["list_name"] == "leader"]
    hist = data.get("gate_history") or []
    out = [f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,"
           f"initial-scale=1'><title>Equity {_e(day)}</title><style>{CSS}</style></head><body><main>",
           f"<h1>Is it safe to buy stocks? · {_e(day.strftime('%a %b %d') if isinstance(day, date) else day)}</h1>",
           gate_block(g), "<p class='note'>Tap a tile to see what it measures. Rule v1, not yet tested.</p>"]
    left, right = [], []
    rib = ribbon_svg(hist)
    if rib:
        left += ["<h2>Gate, last 60 sessions</h2>", rib]
    b = data.get("buckets")
    if b:
        left += ["<h2>Where stocks sit vs their 1-year high</h2>",
                 "<p class='note'>More green than 20 sessions ago = healing. More red = damage spreading.</p>",
                 buckets_svg(b["now"], b["then"])]
    if hist:
        ds = [r["date"] for r in hist]
        right += ["<h2>Typical stock vs its high - 1 year</h2>",
                  line_svg(ds, [r.get("median_drawdown") for r in hist], title="median drawdown"),
                  "<h2>Healing (+) or spreading (-)</h2>",
                  line_svg(ds, [r.get("repair_flow") for r in hist], title="repair flow", unit="", bars=True)]
    out.append(f'<div class="two"><div>{"".join(left)}</div><div>{"".join(right)}</div></div>')
    left, right = [], []
    if data.get("tides"):
        left += ["<h2>What the market paid for · 20 sessions</h2>",
                 "<p class='note'>Green momentum = winners kept winning. Red beta = high-beta stocks lagged.</p>",
                 hbars_svg(data["tides"], title="style returns")]
    if lead:
        right += ["<h2>Strongest own moves</h2>",
                  "<p class='note'>Gain over 6 months that market, sector and style tides do NOT explain.</p>",
                  hbars_svg(sorted(((r["symbol"], (r.get("own_move_6m") or 0) * 100) for r in lead[:10]),
                                    key=lambda kv: -kv[1]), title="own move")]
    out.append(f'<div class="two"><div>{"".join(left)}</div><div>{"".join(right)}</div></div>')
    out.append(section("Leaders", "Tap a card for stop, size and the option idea. Red dash = stop, grey dash = "
                       "50-day line.", lead, data, 6))
    out.append(section("Coiled springs", "Shaded band = the box. Waiting for calls to gain value, then a break up.",
                       [r for r in rows if r["list_name"] == "coiled"], data, 6))
    out.append(section("Breakouts today", "Closed above the box top on 1.5x+ normal volume.",
                       [r for r in rows if r["list_name"] == "breakout"], data, 6))
    out.append("<h2>Hedge menu</h2>" + "".join(
        f"<details><summary><b>{_e(n)}</b></summary><p>{_e(w)}</p></details>" for n, w in HEDGE_MENU))
    out.append(f"<p class='note'>{_e(HEDGE_RULE)}</p><p class='note'>S&amp;P 500 members as they were on each date "
               "+ S&amp;P 400/600 today's members. Descriptive only - nothing here places a trade.</p></main></body></html>")
    return "".join(out)

