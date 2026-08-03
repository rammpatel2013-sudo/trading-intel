"""Watchlist rolling Net GEX + Net DEX overview (re-openable HTML).

For every active ticker in the research watchlist that has collected greeks
history, draws two small rolling line charts -- daily Net GEX and daily Net DEX
-- over a lookback window. Reads our stored NAS data read-only via ``.env``
DATABASE_URL and writes a single self-contained
``reports/watchlist_gex_dex_<date>.html`` (charts drawn as inline SVG, so the
page renders with no network / in preview).

Per-day value = the last snapshot of each calendar day. The renderer flags
suspect data so the lines never imply clean continuity they don't have:

  * gap   -- a calendar jump > ``--gap-days`` between consecutive snapshots
             (e.g. the Jun 5-13 2026 NAS outage) is drawn as a dashed segment.
             Gaps are near-universal (one shared outage) so they are NOT badged
             per card -- only drawn and called out in the global note.
  * stale -- a frozen value (identical to the prior day) or a corrupt ATM IV
             (> ``--iv-max``, e.g. the Jun 19-21 2026 recovery artifacts) is
             drawn as an amber point, its segments dashed, and the card gets a
             "data caveats" badge.

Regime descriptors only -- never a signal (FlashAlpha rule 4).

Run (Windows; DATABASE_URL pointed at the NAS):
    .venv\\Scripts\\python scripts\\watchlist_gex_dex_report.py
    .venv\\Scripts\\python scripts\\watchlist_gex_dex_report.py --days 30
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import create_engine, text

from trading_intel.config import get_settings

_REPO_ROOT = Path(__file__).resolve().parents[1]
_OUT = _REPO_ROOT / "reports"

BLUE, RED, AMBER, GRID, AXIS, BG_CARD = "#4f9cf9", "#e5484d", "#f5a623", "#1e222a", "#8a8f98", "#171a21"


def _fmt(v: float) -> str:
    a = abs(v)
    if a >= 1e9:
        return f"{v / 1e9:.1f}B"
    if a >= 1e6:
        return f"{v / 1e6:.1f}M"
    if a >= 1e3:
        return f"{v / 1e3:.1f}k"
    return f"{v:.0f}"


def _spark(dates, values, stale, *, gap_days, w=400, h=150):
    ml, mr, mt, mb = 52, 10, 12, 26
    pw, ph = w - ml - mr, h - mt - mb
    vmin, vmax = min(values), max(values)
    if vmin == vmax:
        vmin, vmax = vmin - 1, vmax + 1
    pad = (vmax - vmin) * 0.12
    vmin, vmax = vmin - pad, vmax + pad
    vmin, vmax = min(vmin, 0.0), max(vmax, 0.0)
    n = len(values)

    def X(i):
        return ml + (pw * i / (n - 1) if n > 1 else pw / 2)

    def Y(v):
        return mt + ph * (vmax - v) / (vmax - vmin)

    # int coords + CSS classes + ONE polyline per solid/dashed run (was one <line> per
    # segment and inline fill/stroke on every element) -> ~55% smaller HTML, so mobile
    # Telegram can open it. Pixel-identical render; classes are defined in _page's <style>.
    pts = [(round(X(i)), round(Y(v))) for i, v in enumerate(values)]
    parts = [f'<rect width="{w}" height="{h}" fill="{BG_CARD}"/>']
    for tv in sorted({vmin + pad, 0.0, vmax - pad}):
        yy = round(Y(tv))
        parts.append(f'<line class="g" x1="{ml}" y1="{yy}" x2="{w - mr}" y2="{yy}"/>')
        parts.append(f'<text class="xe" x="{ml - 5}" y="{yy + 3}">{_fmt(tv)}</text>')
    yz = round(Y(0.0))
    parts.append(f'<line class="z" x1="{ml}" y1="{yz}" x2="{w - mr}" y2="{yz}"/>')
    suspect = [stale[i] or stale[i + 1] or (dates[i + 1] - dates[i]).days > gap_days for i in range(n - 1)]
    i = 0
    while i < n - 1:
        j = i
        while j < n - 1 and suspect[j] == suspect[i]:
            j += 1
        cls = "l d" if suspect[i] else "l"
        run = " ".join(f"{x},{y}" for x, y in pts[i:j + 1])
        parts.append(f'<polyline class="{cls}" points="{run}"/>')
        i = j
    for i, (px, py) in enumerate(pts):
        cls = "s" if stale[i] else ("n" if values[i] < 0 else "p")
        parts.append(f'<circle class="{cls}" cx="{px}" cy="{py}" r="2.6"/>')
    if n:
        parts.append(f'<text class="xs" x="{round(X(0))}" y="{h - 8}">{dates[0]:%m-%d}</text>')
        parts.append(f'<text class="xe" x="{round(X(n - 1))}" y="{h - 8}">{dates[-1]:%m-%d}</text>')
    body = "".join(parts)
    return f'<svg viewBox="0 0 {w} {h}" width="100%" xmlns="http://www.w3.org/2000/svg">{body}</svg>'


def _dedupe_last_per_day(rows):
    by_day = {}
    for ts, gex, dex, iv in rows:
        by_day[ts.date()] = (gex, dex, iv)
    days = sorted(by_day)
    gex = [float(by_day[d][0]) if by_day[d][0] is not None else 0.0 for d in days]
    dex = [float(by_day[d][1]) if by_day[d][1] is not None else 0.0 for d in days]
    iv = [by_day[d][2] for d in days]
    return days, gex, dex, iv


def _stale_flags(values, iv, *, iv_max):
    flags = []
    for i, v in enumerate(values):
        bad_iv = iv[i] is not None and float(iv[i]) > iv_max
        frozen = i > 0 and v == values[i - 1]
        flags.append(bool(bad_iv or frozen))
    return flags


def _card(sym, gsvg, dsvg, gex_last, dex_last, stale):
    flag = ' <span class="warn">data caveats</span>' if stale else ""
    gcol = RED if gex_last < 0 else BLUE
    dcol = RED if dex_last < 0 else BLUE
    return (
        f'<div class="card"><div class="hd">{sym}{flag}</div><div class="charts">'
        f'<div class="ch"><div class="lbl">Net GEX <b style="color:{gcol}">{_fmt(gex_last)}</b></div>{gsvg}</div>'
        f'<div class="ch"><div class="lbl">Net DEX <b style="color:{dcol}">{_fmt(dex_last)}</b></div>{dsvg}</div>'
        f'</div></div>'
    )


def _page(body, *, generated, days, n, n_short, skip_note):
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Watchlist - Rolling Net GEX &amp; DEX</title><style>
body{{background:#0f1115;color:#e6e6e6;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;margin:0;padding:24px}}
h1{{font-size:20px;margin:0 0 4px}}.sub{{color:#8a8f98;font-size:13px;margin:0 0 18px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(440px,1fr));gap:14px}}
.card{{background:{BG_CARD};border:1px solid #262b34;border-radius:10px;padding:12px}}
.hd{{font-size:15px;font-weight:600;margin-bottom:6px}}
.charts{{display:grid;grid-template-columns:1fr 1fr;gap:8px}}
.lbl{{font-size:11px;color:#8a8f98;margin-bottom:2px}}.lbl b{{font-weight:600}}
.warn{{font-size:10px;color:{AMBER};border:1px solid {AMBER};border-radius:4px;padding:1px 5px;margin-left:6px;vertical-align:middle}}
.note{{color:#8a8f98;font-size:12px;margin-top:18px;line-height:1.5}}.note b{{color:#c7ccd4}}
svg{{display:block}}.g{{stroke:{GRID}}}.z{{stroke:{RED};stroke-width:1;stroke-dasharray:3,3;opacity:.55}}.l{{fill:none;stroke:{BLUE};stroke-width:1.6}}.d{{stroke-dasharray:6,4}}.p{{fill:{BLUE}}}.n{{fill:{RED}}}.s{{fill:{AMBER}}}.xe{{fill:{AXIS};font-size:9px;text-anchor:end}}.xs{{fill:{AXIS};font-size:9px;text-anchor:start}}
</style></head><body>
<h1>Watchlist - Rolling Net GEX &amp; DEX</h1>
<p class="sub">{n} tickers &middot; {days}-day window &middot; last snapshot per day &middot; {n_short} currently short-gamma (net GEX &lt; 0){skip_note} &middot; generated {generated}</p>
<div class="grid">{body}</div>
<p class="note"><b>Reading it:</b> Net GEX above 0 = long-gamma (dealers dampen moves), below 0 = short-gamma (amplifying) -- red points mark negative readings. Net DEX is net directional (delta) exposure. Short-gamma names sort to the top.<br>
<b>Data caveats:</b> dashed segments span a calendar gap (e.g. the Jun 5-13 2026 NAS outage); amber points are frozen/stale values or corrupt ATM IV (e.g. Jun 19-21 2026). Descriptors only, not signals (FlashAlpha rule 4).</p>
</body></html>"""


def build(*, days=90, gap_days=4, iv_max=2.0, out=None):
    eng = create_engine(get_settings().DATABASE_URL)
    with eng.connect() as cx:
        symbols = [r[0] for r in cx.execute(text(
            "SELECT DISTINCT symbol FROM watchlist_entries WHERE active IS TRUE ORDER BY symbol"))]
        cards = []
        skipped = []
        for sym in symbols:
            rows = [tuple(r) for r in cx.execute(text(
                "SELECT ts, gex_total, dex_total, atm_iv FROM greeks_snapshots "
                "WHERE symbol=:s AND ts >= now() - (:d || ' days')::interval ORDER BY ts ASC"),
                {"s": sym, "d": days})]
            d, gex, dex, iv = _dedupe_last_per_day(rows)
            if len(d) < 2:
                skipped.append(sym)
                continue
            gflag = _stale_flags(gex, iv, iv_max=iv_max)
            dflag = _stale_flags(dex, iv, iv_max=iv_max)
            stale_card = any(gflag) or any(dflag)
            gsvg = _spark(d, gex, gflag, gap_days=gap_days)
            dsvg = _spark(d, dex, dflag, gap_days=gap_days)
            cards.append((sym, _card(sym, gsvg, dsvg, gex[-1], dex[-1], stale_card), gex[-1] < 0))
    cards.sort(key=lambda c: (not c[2], c[0]))
    n_short = sum(1 for c in cards if c[2])
    body = "".join(c[1] for c in cards)
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    skip_note = (f" &middot; {len(skipped)} watchlist names skipped (no greeks history)" if skipped else "")
    out = out or (_OUT / f"watchlist_gex_dex_{date.today():%Y-%m-%d}.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_page(body, generated=generated, days=days, n=len(cards),
                          n_short=n_short, skip_note=skip_note), encoding="utf-8")
    print(f"Wrote {out} ({len(cards)} tickers, {len(skipped)} skipped).")
    return out


def main():
    p = argparse.ArgumentParser(description="Watchlist rolling Net GEX + Net DEX HTML overview.")
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--gap-days", type=int, default=4)
    p.add_argument("--iv-max", type=float, default=2.0)
    p.add_argument("--out", default=None)
    a = p.parse_args()
    build(days=a.days, gap_days=a.gap_days, iv_max=a.iv_max, out=Path(a.out) if a.out else None)


if __name__ == "__main__":
    main()
