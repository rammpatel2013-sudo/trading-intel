"""Index-ETF constant-maturity forward-IV history (re-openable HTML).

For every symbol with stored ``iv_tenor_snapshots`` rows (QQQ / SPY / SPX), draws
one card per symbol with a small multi-line chart per constant-maturity tenor
(1M / 3M). Each chart overlays the ATM IV and the 15Δ / 25Δ call and put wings
across the lookback window, so the historical skew/term evolution reads at a
glance. Reads our stored NAS data read-only via ``.env`` DATABASE_URL and writes
a single self-contained ``reports/iv_tenor_<date>.html`` (charts drawn as inline
SVG, so the page renders with no network / in preview).

The renderer flags suspect data so the lines never imply clean continuity they
don't have:

  * gap   -- a calendar jump > ``--gap-days`` between consecutive snapshots is
             drawn as a dashed segment (and likewise across a missing value).
  * stale -- a frozen value (identical to the prior day) or a corrupt IV
             (> ``--iv-max``) is drawn as a hollow point and badges the card.

Regime descriptors only -- never a signal (FlashAlpha rule 4).

Run (Windows; DATABASE_URL pointed at the NAS):
    .venv\\Scripts\\python scripts\\iv_tenor_report.py
    .venv\\Scripts\\python scripts\\iv_tenor_report.py --days 120
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import create_engine, text

from trading_intel.config import get_settings

_REPO_ROOT = Path(__file__).resolve().parents[1]
_OUT = _REPO_ROOT / "reports"

GRID, AXIS, BG_CARD = "#1e222a", "#8a8f98", "#171a21"

# One colour per series; order drives the legend.
_SERIES = (
    ("iv_atm", "ATM (50Δ)", "#e6e6e6"),
    ("iv_put_25d", "Put 25Δ", "#e5484d"),
    ("iv_call_25d", "Call 25Δ", "#30a46c"),
    ("iv_put_15d", "Put 15Δ", "#f5a623"),
    ("iv_call_15d", "Call 15Δ", "#4f9cf9"),
)

_PREFERRED = ("QQQ", "SPY", "SPX")


def _pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def _stale_flags(values: list[float | None], *, iv_max: float) -> list[bool]:
    flags: list[bool] = []
    prev: float | None = None
    for v in values:
        bad = v is not None and (v > iv_max or v <= 0)
        frozen = v is not None and prev is not None and v == prev
        flags.append(bool(bad or frozen))
        prev = v
    return flags


def _chart(dates, series, *, gap_days, iv_max, w=420, h=190):
    """Multi-line IV chart. ``series`` = list of (key, label, colour, values)."""
    ml, mr, mt, mb = 46, 10, 12, 26
    pw, ph = w - ml - mr, h - mt - mb
    present = [v for _, _, _, vals in series for v in vals if v is not None and 0 < v <= iv_max]
    if not present:
        return f'<svg viewBox="0 0 {w} {h}" width="100%"><rect width="{w}" height="{h}" fill="{BG_CARD}"/></svg>'
    vmin, vmax = min(present), max(present)
    if vmin == vmax:
        vmin, vmax = vmin - 0.01, vmax + 0.01
    pad = (vmax - vmin) * 0.15
    vmin, vmax = vmin - pad, vmax + pad
    n = len(dates)

    def sx(i):
        return ml + (pw * i / (n - 1) if n > 1 else pw / 2)

    def sy(v):
        return mt + ph * (vmax - v) / (vmax - vmin)

    parts = [f'<rect width="{w}" height="{h}" fill="{BG_CARD}"/>']
    for tv in (vmin + pad, (vmin + vmax) / 2, vmax - pad):
        yy = sy(tv)
        parts.append(f'<line x1="{ml}" y1="{yy:.1f}" x2="{w - mr}" y2="{yy:.1f}" stroke="{GRID}"/>')
        parts.append(f'<text x="{ml - 5}" y="{yy + 3:.1f}" fill="{AXIS}" font-size="9" text-anchor="end">{_pct(tv)}</text>')

    for _key, _label, col, vals in series:
        flags = _stale_flags(vals, iv_max=iv_max)
        last_xy: tuple[float, float] | None = None
        last_i = -1
        for i, v in enumerate(vals):
            if v is None or not (0 < v <= iv_max):
                continue
            px, py = sx(i), sy(v)
            if last_xy is not None:
                gap = (dates[i] - dates[last_i]).days > gap_days or (i - last_i) > 1
                suspect = gap or flags[i] or flags[last_i]
                dash = ' stroke-dasharray="5,4"' if suspect else ""
                parts.append(
                    f'<line x1="{last_xy[0]:.1f}" y1="{last_xy[1]:.1f}" x2="{px:.1f}" y2="{py:.1f}" '
                    f'stroke="{col}" stroke-width="1.5"{dash}/>'
                )
            fill = BG_CARD if flags[i] else col
            parts.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="2.2" fill="{fill}" stroke="{col}" stroke-width="1"/>')
            last_xy, last_i = (px, py), i

    if n:
        parts.append(f'<text x="{sx(0):.1f}" y="{h - 8}" fill="{AXIS}" font-size="9" text-anchor="start">{dates[0]:%m-%d}</text>')
        parts.append(f'<text x="{sx(n - 1):.1f}" y="{h - 8}" fill="{AXIS}" font-size="9" text-anchor="end">{dates[-1]:%m-%d}</text>')
    return f'<svg viewBox="0 0 {w} {h}" width="100%" xmlns="http://www.w3.org/2000/svg">{"".join(parts)}</svg>'


def _legend() -> str:
    items = "".join(
        f'<span class="leg"><i style="background:{col}"></i>{label}</span>'
        for _key, label, col in _SERIES
    )
    return f'<div class="legend">{items}</div>'


def _tenor_block(label: str, dates, rows_by_key, *, gap_days, iv_max) -> str:
    series = [(key, lbl, col, rows_by_key.get(key, [])) for key, lbl, col in _SERIES]
    atm = rows_by_key.get("iv_atm", [])
    last_atm = next((v for v in reversed(atm) if v is not None), None)
    tag = f' &middot; ATM <b>{_pct(last_atm)}</b>' if last_atm is not None else ""
    svg = _chart(dates, series, gap_days=gap_days, iv_max=iv_max)
    return f'<div class="ch"><div class="lbl">{label}{tag}</div>{svg}</div>'


def _card(sym, blocks, *, stale) -> str:
    flag = ' <span class="warn">data caveats</span>' if stale else ""
    return (
        f'<div class="card"><div class="hd">{sym}{flag}</div>'
        f'<div class="charts">{blocks}</div></div>'
    )


def _page(body, *, generated, days, n, skip_note) -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Index ETF - Constant-Maturity Forward IV</title><style>
body{{background:#0f1115;color:#e6e6e6;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;margin:0;padding:24px}}
h1{{font-size:20px;margin:0 0 4px}}.sub{{color:#8a8f98;font-size:13px;margin:0 0 14px}}
.legend{{margin:0 0 16px;font-size:11px;color:#c7ccd4}}
.leg{{margin-right:14px;white-space:nowrap}}.leg i{{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:middle}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(460px,1fr));gap:14px}}
.card{{background:{BG_CARD};border:1px solid #262b34;border-radius:10px;padding:12px}}
.hd{{font-size:15px;font-weight:600;margin-bottom:6px}}
.charts{{display:grid;grid-template-columns:1fr 1fr;gap:8px}}
.lbl{{font-size:11px;color:#8a8f98;margin-bottom:2px}}.lbl b{{color:#e6e6e6;font-weight:600}}
.warn{{font-size:10px;color:#f5a623;border:1px solid #f5a623;border-radius:4px;padding:1px 5px;margin-left:6px;vertical-align:middle}}
.note{{color:#8a8f98;font-size:12px;margin-top:18px;line-height:1.5}}.note b{{color:#c7ccd4}}
</style></head><body>
<h1>Index ETF - Constant-Maturity Forward IV</h1>
<p class="sub">{n} symbols &middot; {days}-day window &middot; ATM + 15&Delta;/25&Delta; wings at fixed 1M / 3M tenors{skip_note} &middot; generated {generated}</p>
{_legend()}
<div class="grid">{body}</div>
<p class="note"><b>Reading it:</b> each line is a constant-maturity (fixed-DTE) implied vol, interpolated in total-variance space so it doesn't sawtooth as expiries roll. Put 25&Delta; above Call 25&Delta; is the usual equity put-skew (a 25&Delta; risk reversal = Put 25&Delta; &minus; Call 25&Delta;).<br>
<b>Data caveats:</b> dashed segments span a calendar gap or a missing interpolation; hollow points are frozen/stale or out-of-range IV. Descriptors only, not signals (FlashAlpha rule 4).</p>
</body></html>"""


def build(*, days=120, gap_days=4, iv_max=2.0, out=None):
    eng = create_engine(get_settings().DATABASE_URL)
    keys = [k for k, _, _ in _SERIES]
    col_sql = ", ".join(keys)
    with eng.connect() as cx:
        symbols = [r[0] for r in cx.execute(text(
            "SELECT DISTINCT symbol FROM iv_tenor_snapshots ORDER BY symbol"))]
        symbols = [s for s in _PREFERRED if s in symbols] + [s for s in symbols if s not in _PREFERRED]
        cards: list[tuple[str, str]] = []
        skipped: list[str] = []
        for sym in symbols:
            rows = [tuple(r) for r in cx.execute(text(
                # col_sql is a join of hardcoded _SERIES keys, not user input — safe.
                f"SELECT ts, tenor_dte, {col_sql} FROM iv_tenor_snapshots "  # noqa: S608
                "WHERE symbol=:s AND ts >= now() - (:d || ' days')::interval ORDER BY ts ASC"),
                {"s": sym, "d": days})]
            if not rows:
                skipped.append(sym)
                continue
            tenors = sorted({r[1] for r in rows})
            blocks = []
            stale_card = False
            for tenor in tenors:
                trows = [r for r in rows if r[1] == tenor]
                dates = [r[0] for r in trows]
                rows_by_key = {k: [r[2 + i] for r in trows] for i, k in enumerate(keys)}
                for k in keys:
                    if any(_stale_flags(rows_by_key[k], iv_max=iv_max)):
                        stale_card = True
                label = f"{tenor}d ({'1M' if tenor <= 45 else '3M' if tenor <= 135 else f'{tenor}d'})"
                blocks.append(_tenor_block(label, dates, rows_by_key, gap_days=gap_days, iv_max=iv_max))
            cards.append((sym, _card(sym, "".join(blocks), stale=stale_card)))
    body = "".join(c[1] for c in cards)
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    skip_note = (f" &middot; {len(skipped)} symbol(s) skipped (no history)" if skipped else "")
    out = out or (_OUT / f"iv_tenor_{date.today():%Y-%m-%d}.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_page(body, generated=generated, days=days, n=len(cards), skip_note=skip_note),
                   encoding="utf-8")
    print(f"Wrote {out} ({len(cards)} symbols, {len(skipped)} skipped).")
    return out


def main():
    p = argparse.ArgumentParser(description="Index-ETF constant-maturity forward-IV HTML history.")
    p.add_argument("--days", type=int, default=120)
    p.add_argument("--gap-days", type=int, default=4)
    p.add_argument("--iv-max", type=float, default=2.0)
    p.add_argument("--out", default=None)
    a = p.parse_args()
    build(days=a.days, gap_days=a.gap_days, iv_max=a.iv_max, out=Path(a.out) if a.out else None)


if __name__ == "__main__":
    main()
