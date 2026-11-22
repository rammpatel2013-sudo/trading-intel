"""
norseman_report.py - created 2026-11-22. Norseman divergence page (phone-first, every number and chart in the HTML).

Mithil 2026-11-22: "Telegram report weekly and during weekday if its warning means orange. I don't need to know if
it's green. Show me visual because divergence is imp. His theory of using multiple index, their correlation,
breadth divergence and how it fixed in bullish or bearish way is key."

The page:
  1. Status colour - green (rails confirmed the high) / orange (divergence open, or SPX near / testing the line) /
     red (weekly close below the Bull/Bear line while breadth is diverging, or a 2nd 10% weekly close).
  2. Rails chart - SPX, RSP, IWM, S&P 500 A-D line, S&P 600 A-D line, each drawn as its position inside its own
     range since the window start (100 = its peak). A dot marks each peak, so you see who topped first.
  3. Gap clock - sessions between the earliest rail peak and the SPX high, against the 14 / 21 / 53 marks.
  4. Together or apart - 20-day correlation of each rail's daily change with SPX's.
  5. How it is resolving - healing bullish / divergence open / resolving bearish / confirmed.

Telegram: Fridays (week close) always; other days only when orange or red. Descriptor only (rule 4).
Run after the norseman_regime job (DSM Mon-Fri, 18:35): run_script.sh norseman_report --telegram
"""
from __future__ import annotations

import argparse
import html
import math
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

COLORS = {"SPX": "#14181f", "RSP": "#1d6fd1", "IWM": "#b7791f", "A-D 500": "#0f7b55", "A-D 600": "#8e44ad"}
NAMES = {"SPX": "SPX (price)", "RSP": "RSP — average stock", "IWM": "IWM — small caps",
         "A-D 500": "A-D line — S&P 500", "A-D 600": "A-D line — S&P 600 small caps"}
STATUS_COL = {"green": "#0f7b55", "orange": "#c2620a", "red": "#b42318"}
GAP_MARKS = (14, 21, 53)


# ------------------------------------------------------------------ pure helpers
def scaled(series: list[tuple[date, float]]) -> list[tuple[date, float]]:
    """Position of each value inside the series' own min..max (0..100). 100 = its peak."""
    if not series:
        return []
    vals = [v for _, v in series]
    lo, hi = min(vals), max(vals)
    span = hi - lo
    return [(d, 50.0 if span == 0 else (v - lo) / span * 100.0) for d, v in series]


def peak_of(series: list[tuple[date, float]]) -> tuple[date, float] | None:
    return max(series, key=lambda p: (p[1], p[0])) if series else None


def changes(series: list[tuple[date, float]], *, diff: bool = False) -> dict[date, float]:
    """Daily change: % for prices, plain difference for an A-D line (it is a running count and can be <= 0)."""
    out = {}
    for (_d0, v0), (d1, v1) in zip(series, series[1:]):
        out[d1] = v1 - v0 if diff or v0 == 0 else v1 / v0 - 1.0
    return out


def _corr(a: list[float], b: list[float]) -> float | None:
    n = len(a)
    if n < 5:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va <= 0 or vb <= 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb)


def rolling_corr(rail: list[tuple[date, float]], spx: list[tuple[date, float]], n: int = 20, *,
                 diff: bool = False) -> list[tuple[date, float]]:
    """20-day correlation of the rail's daily change with SPX's daily change, on shared dates."""
    cr, cs = changes(rail, diff=diff), changes(spx)
    days = sorted(set(cr) & set(cs))
    out = []
    for i in range(n - 1, len(days)):
        w = days[i - n + 1:i + 1]
        c = _corr([cr[d] for d in w], [cs[d] for d in w])
        if c is not None:
            out.append((days[i], c))
    return out


def sessions_between(days: list[date], a: date, b: date) -> int:
    """Signed count of sessions from a to b on the SPX calendar."""
    ia = sum(1 for d in days if d <= a)
    ib = sum(1 for d in days if d <= b)
    return ib - ia


def read_rails(series: dict[str, list[tuple[date, float]]], high_date: date, days: list[date],
               confirm: int = 5) -> list[dict]:
    """Per rail: peak date, gap (sessions its peak came BEFORE the SPX high), % of its range now, diverging."""
    out = []
    for k, s in series.items():
        if k == "SPX" or len(s) < 5:
            continue
        pk = peak_of(s)
        sc = scaled(s)
        gap = sessions_between(days, pk[0], high_date)
        out.append({"key": k, "peak_date": pk[0], "gap": gap, "now_pos": sc[-1][1],
                    "diverging": gap > confirm and s[-1][1] < pk[1],
                    "new_high_after": pk[0] > high_date})
    return out


def status(*, below_line: bool, week_close: bool, n_closes: int, dist_to_line: float, tested_recently: bool,
           rails: list[dict]) -> tuple[str, list[str]]:
    core = [r for r in rails if r["key"] in ("RSP", "A-D 500") and r["diverging"]]
    any_div = [r for r in rails if r["diverging"]]
    why = []
    if n_closes >= 2:
        why.append(f"{n_closes} weekly closes below the line this bull — his rule: a 2nd one ends the bull")
    if below_line and week_close and core:
        why.append("Weekly close below the Bull/Bear line while breadth is diverging = bear-prepare")
    if why:
        return "red", why
    if any_div:
        why.append("Breadth divergence open: " + ", ".join(NAMES[r["key"]] for r in any_div))
    if below_line:
        why.append("SPX is below the Bull/Bear line")
    elif dist_to_line <= 0.05:
        why.append(f"SPX is only {dist_to_line:.1%} above the Bull/Bear line")
    if tested_recently:
        why.append("SPX tested the line in the last 5 sessions")
    return ("orange", why) if why else ("green", ["Breadth made its high with SPX; SPX well above the line"])


def resolution(*, below_line: bool, rails: list[dict]) -> tuple[str, str]:
    div = [r for r in rails if r["diverging"]]
    lead = [r for r in rails if r["new_high_after"]]
    if below_line and div:
        return "Resolving BEARISH", ("Price broke the line while breadth is still below its peaks — the divergence is "
                                     "being settled to the downside (his bear-prepare case).")
    if div and lead:
        return "Mixed", "Some breadth rails made new highs after SPX, others are still lagging."
    if div:
        g = max(r["gap"] for r in div)
        return "Divergence OPEN", (f"Breadth topped {g} sessions before SPX. It heals bullish if the lagging rails make "
                                   "new highs; it resolves bearish on a weekly close below the line.")
    if lead:
        return "Healing BULLISH", "Breadth is making new highs after SPX's high — the market under the hood is leading."
    return "Confirmed", "Breadth peaked together with SPX — no divergence to resolve."


def should_send(color: str, week_close: bool) -> bool:
    return week_close or color in ("orange", "red")


# ------------------------------------------------------------------ drawing
def _x(i: int, n: int, w: float, pad: float) -> float:
    return pad + (w - 2 * pad) * (i / max(1, n - 1))


def rails_svg(series_scaled: dict[str, list[tuple[date, float]]], days: list[date], high_date: date) -> str:
    W, H, P = 360, 230, 14
    idx = {d: i for i, d in enumerate(days)}
    n = len(days)
    out = [f"<svg viewBox='0 0 {W} {H}' width='100%' role='img' aria-label='breadth rails chart'>"]
    for g in (0, 50, 100):
        y = P + (H - 2 * P) * (1 - g / 100)
        out.append(f"<line x1='{P}' x2='{W - P}' y1='{y:.1f}' y2='{y:.1f}' stroke='#d9dee5'/>")
    if high_date in idx:
        x = _x(idx[high_date], n, W, P)
        out.append(f"<line x1='{x:.1f}' x2='{x:.1f}' y1='{P}' y2='{H - P}' stroke='#14181f' stroke-dasharray='4 3'/>"
                   f"<text x='{x + 3:.1f}' y='{P + 10}' font-size='12' fill='#14181f'>SPX high</text>")
    for k, s in series_scaled.items():
        pts = [(_x(idx[d], n, W, P), P + (H - 2 * P) * (1 - v / 100)) for d, v in s if d in idx]
        if len(pts) < 2:
            continue
        sw = 3 if k == "SPX" else 2.2
        out.append(f"<polyline fill='none' stroke='{COLORS[k]}' stroke-width='{sw}' points='"
                   + " ".join(f"{x:.1f},{y:.1f}" for x, y in pts) + "'/>")
        pk = peak_of(s)
        if pk and pk[0] in idx:
            px = _x(idx[pk[0]], n, W, P)
            out.append(f"<circle cx='{px:.1f}' cy='{P:.1f}' r='5' fill='{COLORS[k]}' stroke='#fff' stroke-width='1.5'/>")
    out.append("</svg>")
    return "".join(out)


def gap_svg(gap: int) -> str:
    W, H, mx = 360, 64, 60
    g = max(0, min(gap, mx))
    out = [f"<svg viewBox='0 0 {W} {H}' width='100%' role='img' aria-label='gap clock'>",
           f"<rect x='10' y='18' width='340' height='16' rx='8' fill='#eef1f5'/>",
           f"<rect x='10' y='18' width='{340 * g / mx:.1f}' height='16' rx='8' fill='#c2620a'/>"]
    for m in GAP_MARKS:
        x = 10 + 340 * m / mx
        out.append(f"<line x1='{x:.1f}' x2='{x:.1f}' y1='12' y2='40' stroke='#14181f'/>"
                   f"<text x='{x:.1f}' y='56' font-size='13' text-anchor='middle' fill='#14181f'>{m}</text>")
    out.append("</svg>")
    return "".join(out)


def spark_svg(vals: list[float]) -> str:
    W, H = 120, 34
    if len(vals) < 2:
        return ""
    pts = " ".join(f"{4 + (W - 8) * i / (len(vals) - 1):.1f},{H / 2 - v * (H / 2 - 3):.1f}" for i, v in enumerate(vals))
    return (f"<svg viewBox='0 0 {W} {H}' width='{W}' height='{H}'><line x1='0' x2='{W}' y1='{H / 2}' y2='{H / 2}' "
            f"stroke='#d9dee5'/><polyline fill='none' stroke='#1d6fd1' stroke-width='2' points='{pts}'/></svg>")


def page(*, as_of: date, spx_close: float, line: float, dist: float, ath: float, ath_date: date, color: str,
         why: list[str], res: tuple[str, str], rails: list[dict], series_scaled: dict, days: list[date],
         corr: dict[str, list[tuple[date, float]]], window_start: date, n_closes: int) -> str:
    e = html.escape
    md = lambda d: f"{d.month}/{d.day}"  # noqa: E731
    word = {"green": "GREEN — all clear", "orange": "ORANGE — warning", "red": "RED — bear-prepare"}[color]
    side = "above" if dist >= 0 else "below"
    h = ["<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
         "<title>Norseman — breadth divergence</title><style>body{font:18px/1.45 system-ui,sans-serif;margin:12px;"
         "color:#14181f;background:#fff;max-width:640px}h1{font-size:25px;margin:4px 0}h2{font-size:21px;margin:22px 0 6px}"
         ".big{font-size:26px;font-weight:700;color:#fff;border-radius:10px;padding:12px 14px}.n{color:#3d4652;font-size:16px}"
         ".row{display:flex;align-items:center;justify-content:space-between;gap:8px;border-bottom:1px solid #d9dee5;padding:8px 0}"
         ".sw{display:inline-block;width:16px;height:16px;border-radius:4px;margin-right:8px;vertical-align:-2px}"
         "ul{padding-left:20px;margin:6px 0}b.tag{font-size:22px}</style></head><body>"]
    h.append(f"<h1>Norseman check — {as_of:%a} {md(as_of)}</h1>")
    h.append(f"<div class='big' style='background:{STATUS_COL[color]}'>{word}</div><ul>")
    h += [f"<li>{e(w)}</li>" for w in why]
    h.append("</ul>")
    h.append(f"<p>SPX <b>{spx_close:,.0f}</b> · top {ath:,.0f} on {md(ath_date)} · Bull/Bear line <b>{line:,.0f}</b> — "
             f"SPX is <b>{abs(dist):.1%} {side}</b> it · weekly closes below the line this bull: <b>{n_closes}</b></p>")
    h.append(f"<h2>How it is resolving</h2><p><b class='tag'>{e(res[0])}</b><br>{e(res[1])}</p>")
    h.append(f"<h2>Who topped first? (since {md(window_start)})</h2>")
    h.append("<p class='n'>Each line is drawn inside its own range: 100 = its peak (the dot). "
             "His rule: breadth (A-D line, RSP, small caps) topping well before SPX is the warning.</p>")
    h.append(rails_svg(series_scaled, days, ath_date))
    for k in series_scaled:
        r = next((x for x in rails if x["key"] == k), None)
        if k == "SPX":
            txt = f"peak {md(ath_date)}"
        elif r is None:
            txt = "not enough data yet"
        else:
            lag = (f"topped {r['gap']} sessions BEFORE SPX" if r["gap"] > 5 else
                   "new high AFTER SPX" if r["new_high_after"] else "topped with SPX")
            txt = f"peak {md(r['peak_date'])} · {lag} · now at {r['now_pos']:.0f} of 100"
        h.append(f"<div class='row'><span><i class='sw' style='background:{COLORS[k]}'></i>{e(NAMES[k])}</span>"
                 f"<span class='n'>{e(txt)}</span></div>")
    gaps = [r["gap"] for r in rails if r["diverging"]]
    g = max(gaps) if gaps else 0
    h.append(f"<h2>Gap clock: {g} sessions</h2><p class='n'>Sessions between the earliest breadth peak and the SPX "
             "high. His priors: tops need 14+ sessions of warning; 2018 ran 21, 2024-25 ran 53.</p>")
    h.append(gap_svg(g))
    h.append("<p class='n'>Marks: 14 = minimum warning · 21 = 2018 · 53 = 2024-25</p>")
    h.append("<h2>Together or apart?</h2><p class='n'>20-day correlation of each rail's daily move with SPX's. "
             "Near 1 = moving together. A falling line = breadth starting to go its own way.</p>")
    for k, c in corr.items():
        if not c:
            continue
        h.append(f"<div class='row'><span><i class='sw' style='background:{COLORS[k]}'></i>{e(NAMES[k])}</span>"
                 f"<span>{spark_svg([v for _, v in c[-60:]])} <b>{c[-1][1]:.2f}</b></span></div>")
    h.append("<p class='n'>Our data: S&amp;P 500 A-D (not his NYSE common-only line); the S&amp;P 600 A-D history is "
             "short. Descriptor only — not investment advice.</p></body></html>")
    return "".join(h)


# ------------------------------------------------------------------ main
def build(session, as_of: date | None = None) -> tuple[str, str, bool]:
    from trading_intel.strategies import norseman_regime as nr
    from trading_intel.timeutils import eastern_today

    as_of = as_of or eastern_today()
    st = nr.read_state(session, as_of=as_of)
    if st is None:
        raise SystemExit("norseman_report: not enough SPX history")
    bars = nr.spx_bars(session, as_of=as_of)
    days_all = [b.d for b in bars]
    start = nr.rail_window_start(nm_episodes(bars), st.ath_date, days_all[0])
    days = [d for d in days_all if d >= start]
    raw = {
        "SPX": [(b.d, b.close) for b in bars if b.d >= start],
        "RSP": [(d, v) for d, v in nr._closes(session, "RSP", as_of=as_of) if d >= start],
        "IWM": [(d, v) for d, v in nr._closes(session, "IWM", as_of=as_of) if d >= start],
        "A-D 500": [(d, v) for d, v in nr._ad_line(session, as_of=as_of) if d >= start],
        "A-D 600": [(d, v) for d, v in nr._ad_line(session, as_of=as_of, source="fmp_sp600") if d >= start],
    }
    raw = {k: v for k, v in raw.items() if len(v) >= 5}
    rails = read_rails(raw, st.ath_date, days_all)
    recent = days_all[-5:]
    tested = bool(st.last_test and st.last_test.anchor in recent) or st.tapped_today
    color, why = status(below_line=st.spx_close < st.line, week_close=st.week_close, n_closes=len(st.violations),
                        dist_to_line=st.dist_to_line, tested_recently=tested, rails=rails)
    res = resolution(below_line=st.spx_close < st.line, rails=rails)
    corr = {k: rolling_corr(v, raw["SPX"], diff=k.startswith("A-D")) for k, v in raw.items() if k != "SPX"}
    doc = page(as_of=st.as_of, spx_close=st.spx_close, line=st.line, dist=st.dist_to_line, ath=st.ath,
               ath_date=st.ath_date, color=color, why=why, res=res, rails=rails,
               series_scaled={k: scaled(v) for k, v in raw.items()}, days=days, corr=corr, window_start=start,
               n_closes=len(st.violations))
    return doc, color, should_send(color, st.week_close)


def nm_episodes(bars):
    from trading_intel.market import norseman as nm

    return nm.test_episodes(bars)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "reports" / "norseman.html"))
    ap.add_argument("--date")
    ap.add_argument("--telegram", action="store_true", help="send on Fridays, and on weekdays only if orange/red")
    a = ap.parse_args()
    from trading_intel.config import get_settings
    from trading_intel.memory.db import make_session_factory

    st = get_settings()
    with make_session_factory(st)() as s:
        doc, color, send = build(s, date.fromisoformat(a.date) if a.date else None)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8", newline="\n")
    print("norseman_report", color, "send" if send else "quiet", "->", out)
    if a.telegram and send:
        from trading_intel.clients.telegram import TelegramClient

        cap = {"green": "Norseman weekly: GREEN", "orange": "Norseman: ORANGE warning",
               "red": "Norseman: RED - bear-prepare"}[color]
        print("telegram_sent=", TelegramClient(st).send_document(out, caption=cap))


if __name__ == "__main__":
    main()
