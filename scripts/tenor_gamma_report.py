#!/usr/bin/env python3
"""tenor_gamma_report.py - created 2026-11-09. Nightly "Tenor" cockpit tab + ONE Telegram push.

One combined job (gamma by tenor + vega by tenor + daily history + factor cards):
  1. SPX gamma by tenor (BofA "Exhibit 7" style): naive dealer $gamma per 1% move on a
     spot ladder (+/-12%, 25-pt steps) for 1 day / 2 days-1 week / 1 week-1 month /
     > 1 month (business days) + total, from the stored ``oi_chain_eod`` book.
  2. Dealer $vega per vol point by the same buckets (and per expiry), naive sign plus
     the "customers own everything" and flipped conventions.
  3. History: one row per book day appended to reports/tenor/history_SPX.json
     (``--backfill`` rebuilds every day still in ``oi_chain_eod``). No new DB table.
  4. Factor cards (beta, volatility, size, quality, oil beta, rates beta): z-scores with
     +/-2 sigma zones and an honest base rate. Closes from the existing FMP passthrough
     (CVForge client), yfinance fallback.
Writes scripts/cvxmap/data/tenor.json (the cockpit Tenor tab), reports/tenor/tenor_<date>.html,
reports/tenor/factor_cards.json, and sends ONE Telegram message (PNG photo with a caption,
or the HTML document if no PNG could be rendered).

Descriptor only (rule 4): positioning read, not a trade signal.

Run (NAS):  bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_script.sh tenor_gamma_report
Flags: --backfill (rebuild history first) --no-push --no-factors --symbol SPX --out PATH
       --fixture PATH (render from a saved tenor.json, no DB / network)
"""
# ruff: noqa: E501, N806, RUF001, ANN001  (inline SVG/HTML templates; L/R/T/B = plot margins)

from __future__ import annotations

import argparse
import html
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from trading_intel.greeks.tenor_buckets import BUCKETS, DISCLAIMER  # noqa: E402
from trading_intel.market import tenor_read  # noqa: E402

DATA_DIR = REPO / "scripts" / "cvxmap" / "data"
OUT_DIR = REPO / "reports" / "tenor"
ET = ZoneInfo("America/New_York")
HIST_KEEP = 400
COLORS = {
    "1d": "#FF9F43",
    "2d_1w": "#F4D03F",
    "1w_1m": "#3FD0B0",
    "gt_1m": "#9B7BFF",
    "total": "#FFFFFF",
}


# ---------------------------------------------------------------- build
def build_obj(
    session,
    symbol: str = "SPX",
    *,
    settings=None,
    factors: bool = True,
    backfill: bool = False,
    hist_root: Path | None = None,
) -> dict:
    from trading_intel.market import tenor_data as td

    t0 = time.time()
    if backfill:
        rows = td.backfill(session, symbol, hist_root)
        print(f"backfill: {len(rows)} book days rebuilt")
    day = td.compute_day(session, symbol)
    if day.get("found"):
        td.write_history(symbol, [td.history_row(day)], hist_root)
    hist = td.read_history(symbol, hist_root)
    obj = {
        "generated": datetime.now(ET).isoformat(timespec="minutes"),
        "symbol": symbol.upper(),
        "asof": day.get("as_of"),
        "found": bool(day.get("found")),
        "spot": day.get("spot"),
        "gamma": day.get("gamma"),
        "vega": day.get("vega"),
        "history": hist[-HIST_KEEP:],
        "history_since": hist[0]["date"] if hist else None,
        "change_1w": td.change_vs(hist, 5),
        "buckets": [{"key": k, "label": lab} for k, lab in BUCKETS],
        "note": DISCLAIMER,
    }
    if factors:
        obj["factors"] = build_factors(settings)
    obj["read"] = tenor_read.full_read(obj)
    obj["runtime_s"] = round(time.time() - t0, 1)
    return obj


def build_factors(settings=None) -> dict:
    from trading_intel.market.factor_cards import SYMBOLS, build_cards
    from trading_intel.market.factor_prices import load_closes

    try:
        px, src = load_closes(SYMBOLS, settings=settings)
    except Exception as exc:
        return {"cards": [], "error": f"price load failed: {exc}"[:200], "note": DISCLAIMER}
    fac = (
        build_cards(px) if not px.empty else {"cards": [], "error": "no prices", "note": DISCLAIMER}
    )
    fac["sources"] = sorted(set(src.values()))
    fac["history"] = {s: px[s].first_valid_index().date().isoformat() for s in px.columns}
    return fac


def write_json(obj: dict, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(obj, separators=(",", ":"), allow_nan=False, default=str), encoding="utf-8"
    )
    tmp.replace(out)
    return out


# ---------------------------------------------------------------- HTML report (inline SVG, dark)
def _nice_step(span: float, target: int = 5) -> float:
    import math

    if span <= 0:
        return 1.0
    e = 10 ** math.floor(math.log10(span / target))
    for m in (1, 2, 2.5, 5, 10):
        if span / (m * e) <= target:
            return m * e
    return 10 * e


def svg_lines(
    grid: list[float],
    series: list[tuple[str, list[float], str, bool]],
    spot: float,
    w: int = 960,
    h: int = 420,
) -> str:
    L, R, T, B = 78, 18, 16, 46
    allv = [v for _, ys, _, _ in series for v in ys if v is not None] + [0.0]
    lo, hi = min(allv), max(allv)
    pad = (hi - lo) * 0.06 or 1
    lo, hi = lo - pad, hi + pad
    x0, x1 = grid[0], grid[-1]
    X = lambda x: L + (w - L - R) * (x - x0) / (x1 - x0)  # noqa: E731
    Y = lambda y: T + (h - T - B) * (1 - (y - lo) / (hi - lo))  # noqa: E731
    g = []
    st = _nice_step(hi - lo, 6)
    import math

    k = math.ceil(lo / st) * st
    while k <= hi:
        g.append(
            f'<line x1="{L}" x2="{w-R}" y1="{Y(k):.1f}" y2="{Y(k):.1f}" stroke="#2A3F66" stroke-dasharray="2 4"/>'
            f'<text x="{L-8}" y="{Y(k)+5:.1f}" text-anchor="end">{k:,.0f}</text>'
        )
        k += st
    sx = _nice_step(x1 - x0, 8)
    k = math.ceil(x0 / sx) * sx
    while k <= x1:
        g.append(
            f'<line x1="{X(k):.1f}" x2="{X(k):.1f}" y1="{T}" y2="{h-B}" stroke="#2A3F66" stroke-dasharray="2 4"/>'
            f'<text x="{X(k):.1f}" y="{h-B+22}" text-anchor="middle">{k:,.0f}</text>'
        )
        k += sx
    g.append(
        f'<line x1="{L}" x2="{w-R}" y1="{Y(0):.1f}" y2="{Y(0):.1f}" stroke="#DCE6F2" stroke-width="1.5"/>'
    )
    for _name, ys, col, fill in series:
        pts = " ".join(
            f"{X(x):.1f},{Y(y):.1f}" for x, y in zip(grid, ys, strict=False) if y is not None
        )
        if fill:
            g.append(
                f'<polygon points="{X(grid[0]):.1f},{Y(0):.1f} {pts} {X(grid[-1]):.1f},{Y(0):.1f}" '
                f'fill="{col}" fill-opacity=".14"/>'
            )
        g.append(
            f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="{4 if fill else 3}"/>'
        )
    g.append(
        f'<line x1="{X(spot):.1f}" x2="{X(spot):.1f}" y1="{T}" y2="{h-B}" stroke="#FF5C5C" stroke-width="2.5" stroke-dasharray="7 5"/>'
        f'<text x="{X(spot)+8:.1f}" y="{T+18}" style="fill:#FF8A8A;font-weight:700">spot {spot:,.0f}</text>'
    )
    return (
        f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" style="font:15px system-ui;fill:#DCE6F2">'
        f'<style>text{{fill:#DCE6F2}}</style>{"".join(g)}</svg>'
    )


def svg_bars(
    items: list[tuple[str, float, str]], w: int = 960, h: int = 260, unit: str = ""
) -> str:
    L, R, T, B = 210, 130, 10, 10
    mx = max([abs(v) for _, v, _ in items] + [1e-9])
    row = (h - T - B) / max(1, len(items))
    mid = L + (w - L - R) / 2
    half = (w - L - R) / 2
    g = [f'<line x1="{mid}" x2="{mid}" y1="{T}" y2="{h-B}" stroke="#DCE6F2"/>']
    for i, (lab, v, col) in enumerate(items):
        y = T + i * row + row * 0.18
        bh = row * 0.64
        bw = half * abs(v) / mx
        x = mid if v >= 0 else mid - bw
        g.append(
            f'<text x="{L-12}" y="{y+bh/2+6:.1f}" text-anchor="end" style="font-weight:700">{html.escape(lab)}</text>'
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(bw,1):.1f}" height="{bh:.1f}" fill="{col}" rx="3"/>'
            f'<text x="{w-6}" y="{y+bh/2+6:.1f}" text-anchor="end" style="font-weight:700">{v:+,.0f}{unit}</text>'
        )
    return f'<svg viewBox="0 0 {w} {h}" width="100%" style="font:16px system-ui">{"".join(g)}</svg>'


def render_html(obj: dict) -> str:
    g, v = obj.get("gamma") or {}, obj.get("vega") or {}
    parts = []
    if g:
        series = [(lab, g["buckets"][k]["curve"], COLORS[k], False) for k, lab in BUCKETS]
        series.append(("Total", g["total"]["curve"], COLORS["total"], True))
        parts.append(
            "<h2>Dealer gamma vs SPX level, by tenor</h2>"
            f"<p class=sub>{html.escape(g['units'])} · each line = one tenor bucket, white filled = total</p>"
            + legend()
            + svg_lines(g["grid"], series, obj["spot"])
        )
        rows = "".join(
            f"<tr><td><i style='background:{COLORS[k]}'></i>{lab}</td><td>{g['buckets'][k]['at_spot']:+.1f}</td>"
            f"<td>{_pct(g['buckets'][k].get('share_pct'))}</td><td>{_lvl(g['buckets'][k]['zero_cross'])}</td>"
            f"<td>{_n((obj.get('change_1w') or {}).get(k))}</td></tr>"
            for k, lab in BUCKETS
        )
        t = g["total"]
        rows += (
            f"<tr class=tot><td>Total</td><td>{t['at_spot']:+.1f}</td><td>100%</td><td>{_lvl(t['zero_cross'])}</td>"
            f"<td>{_n((obj.get('change_1w') or {}).get('total'))}</td></tr>"
        )
        parts.append(
            "<table><tr><th>Tenor</th><th>At spot $bn</th><th>Share</th><th>Zero-cross</th>"
            f"<th>vs 1 week ago</th></tr>{rows}</table>"
        )
    if v:
        items = [(lab, v["buckets"][k]["naive"], COLORS[k]) for k, lab in BUCKETS]
        parts.append(
            "<h2>Dealer vega by tenor</h2>"
            f"<p class=sub>{html.escape(v['units'])} · calls +, puts − (dealers long calls, short puts)</p>"
            + svg_bars(items, unit=" $mn")
        )
        alt = [(lab, v["buckets"][k]["short_all"], COLORS[k]) for k, lab in BUCKETS]
        parts.append(
            "<p class=sub>Same book if customers own every option (dealers short calls and puts):</p>"
            + svg_bars(alt, h=200, unit=" $mn")
        )
    fac = obj.get("factors") or {}
    if fac.get("cards"):
        cards = "".join(_fcard(c) for c in fac["cards"])
        parts.append(f"<h2>Factor cards</h2><div class=grid>{cards}</div>")
    read = "".join(f"<li>{html.escape(x)}</li>" for x in obj.get("read") or [])
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Tenor gamma {html.escape(str(obj.get('asof')))}</title><style>
body{{background:#0A1426;color:#fff;font:17px/1.5 system-ui,Segoe UI,Arial;margin:0;padding:18px 22px;max-width:1000px}}
h1{{font-size:28px;margin:0 0 4px}}h2{{font-size:22px;margin:22px 0 4px}}.sub{{color:#DCE6F2;font-size:16px;margin:2px 0 8px}}
table{{border-collapse:collapse;width:100%;font-size:17px;margin:8px 0}}td,th{{padding:6px 8px;border-bottom:1px solid #2A3F66;text-align:right}}
td:first-child,th:first-child{{text-align:left}}th{{color:#DCE6F2;font-weight:600}}tr.tot td{{font-weight:800}}
i{{display:inline-block;width:14px;height:14px;border-radius:3px;margin-right:8px;vertical-align:-2px}}
.lg span{{margin-right:16px;font-size:16px}}ul{{padding-left:20px}}li{{margin:4px 0}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:10px}}
.fc{{background:#13213D;border:1px solid #2A3F66;border-radius:10px;padding:10px 12px;font-size:16px}}
.fc b.z{{font-size:30px}}.badge{{border-radius:5px;padding:2px 8px;font-weight:800;color:#0A1426;font-size:14px}}
.foot{{color:#DCE6F2;font-size:15px;margin-top:18px}}</style></head><body>
<h1>{html.escape(obj.get('symbol','SPX'))} gamma &amp; vega by tenor · {html.escape(str(obj.get('asof')))}</h1>
<p class=sub>End-of-day book · spot {obj.get('spot') or 0:,.2f} · built {html.escape(str(obj.get('generated')))} · history since {html.escape(str(obj.get('history_since')))}</p>
<ul>{read}</ul>{''.join(parts)}
<p class=foot>Naive dealer book: dealers long calls, short puts. Black-Scholes, rates and dividends zero, calendar time, each strike's own IV.
Descriptive, {DISCLAIMER}.</p></body></html>"""


def legend() -> str:
    items = [(COLORS[k], lab) for k, lab in BUCKETS] + [(COLORS["total"], "Total")]
    return (
        "<div class=lg>"
        + "".join(f"<span><i style='background:{c}'></i>{lab}</span>" for c, lab in items)
        + "</div>"
    )


def _fcard(c: dict) -> str:
    if not c.get("found"):
        return f"<div class=fc><b>{html.escape(c.get('name',''))}</b><br>No data: {html.escape(c.get('reason',''))}</div>"
    col = {
        "buy": "#3FD0B0",
        "sell": "#FF9F43",
        "nearing_buy": "#A8F0DF",
        "nearing_sell": "#FFD3A6",
    }.get(c["zone"], "#9DB0CA")
    short = f"<br>History only since {c['since']}." if c.get("short_history") else ""
    return (
        f"<div class=fc><b>{html.escape(c['name'])}</b> <span style='color:#DCE6F2'>{html.escape(c['pair'])}</span><br>"
        f"<b class=z>{c['z']:+.1f}</b> z · 2 weeks ago {_n(c.get('z_2w'))} "
        f"<span class=badge style='background:{col}'>{html.escape(c['badge'])}</span><br>"
        f"{html.escape(c['sentence'])}{short}</div>"
    )


def _n(x) -> str:
    return "–" if x is None else f"{x:+.1f}"


def _pct(x) -> str:
    return "–" if x is None else f"{x:.0f}%"


def _lvl(x) -> str:
    return "none" if x is None else f"{x:,.0f}"


# ---------------------------------------------------------------- push
def rasterize(html_path: Path) -> Path | None:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None
    png = html_path.with_suffix(".png")
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(args=["--no-sandbox"])
            pg = b.new_page(viewport={"width": 1000, "height": 900}, device_scale_factor=1.5)
            pg.goto(html_path.resolve().as_uri())
            pg.wait_for_timeout(300)
            pg.screenshot(path=str(png), full_page=True)
            b.close()
        return png
    except Exception as exc:
        print("rasterize failed:", exc)
        return None


def push(obj: dict, html_path: Path, settings) -> bool:
    """ONE Telegram message: the PNG with a caption, else the HTML document."""
    from trading_intel.clients.telegram import TelegramClient

    tg = TelegramClient(settings)
    cap = tenor_read.caption(obj)
    png = rasterize(html_path)
    if png is not None:
        return bool(tg.send_photo(png, caption=cap))
    return bool(tg.send_document(html_path, caption=cap))


def run(args: argparse.Namespace) -> dict:
    if args.fixture:
        obj = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
        obj["read"] = tenor_read.full_read(obj)
        settings = None
    else:
        from trading_intel.config import get_settings
        from trading_intel.memory.db import make_session_factory

        settings = get_settings()
        with make_session_factory(settings)() as s:
            obj = build_obj(
                s,
                args.symbol,
                settings=settings,
                factors=not args.no_factors,
                backfill=args.backfill,
            )
    out = Path(args.out) if args.out else DATA_DIR / "tenor.json"
    write_json(obj, out)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if obj.get("factors"):
        write_json(obj["factors"], OUT_DIR / "factor_cards.json")
    html_path = OUT_DIR / f"tenor_{obj.get('asof') or 'na'}.html"
    html_path.write_text(render_html(obj), encoding="utf-8")
    print(f"tenor.json -> {out}\nreport -> {html_path}")
    for line in obj.get("read") or []:
        print("  " + line)
    if not args.no_push and settings is not None:
        print("telegram_sent=", push(obj, html_path, settings))
    return obj


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--symbol", default="SPX")
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--no-factors", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--fixture")
    run(ap.parse_args())
    return 0


if __name__ == "__main__":
    sys.exit(main())
