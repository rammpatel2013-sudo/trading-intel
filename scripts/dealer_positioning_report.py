"""Dealer positioning — SPX + SPY gamma / charm / vanna / vega by spot, now vs earlier book.

Production (phone-safe) version of the Cowork ``spx-dealer-positioning`` board:
server-side inline SVG, no <script>, no CDN. For each index root it overlays
today's per-strike book on the book from ``--window`` sessions earlier, both priced
on TODAY's spot ladder (``get_profile(as_of=…, spot_ref=…)``), so the shift in the
curves is positioning change, not spot drift.

Index per-strike chains come from the 08:35 ``index_walls_am`` snapshot (indices
are excluded from the EOD persister), so "earlier book" = that root's snapshot N
sessions back. Calls+/puts− convention — an INFERRED dealer book, not cleared
inventory: read shape, flips and zero-lines, not levels. Descriptor only (rule 4).

Run:
    python scripts/dealer_positioning_report.py            # build + push
    python scripts/dealer_positioning_report.py --no-push
"""
from __future__ import annotations

import html
import importlib.util
from datetime import date
from pathlib import Path

from sqlalchemy import select

_SYMS = ("SPX", "SPY")
_WINDOW = 5
_GREEKS = ("gamma", "charm", "vanna", "vega")
_TITLES = {
    "gamma": "Gamma ($bn Δ per 1% move)",
    "charm": "Charm ($bn Δ per day)",
    "vanna": "Vanna ($bn Δ per vol-pt)",
    "vega": "Vega ($bn per vol-pt)",
}
_BG, _PANEL, _TXT, _MUT = "#0f1216", "#161b22", "#e6edf3", "#7a8699"
_C1, _C2 = "#4ea1ff", "#7a8699"


def _svg_mod():
    p = Path(__file__).resolve().parent / "skew_vanna_report.py"
    spec = importlib.util.spec_from_file_location("_skew_vanna_svg", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _at(xs, ys, x):
    for i in range(1, len(xs)):
        if xs[i - 1] <= x <= xs[i]:
            t = (x - xs[i - 1]) / ((xs[i] - xs[i - 1]) or 1)
            return ys[i - 1] + t * (ys[i] - ys[i - 1])
    return None


def _collect(session, sym: str) -> dict:
    from trading_intel.mcp.profile_tool import get_profile
    from trading_intel.memory.models import OiChainEod

    now = get_profile(session, sym, n_points=81, greeks=_GREEKS)
    before = None
    if now.get("found"):
        days = session.execute(
            select(OiChainEod.ts).where(OiChainEod.symbol == sym).distinct()
            .order_by(OiChainEod.ts.desc()).limit(_WINDOW + 1)
        ).scalars().all()
        if len(days) > _WINDOW:
            d = days[-1]
            before = get_profile(session, sym, n_points=81, greeks=_GREEKS,
                                 as_of=d.date() if hasattr(d, "date") else d, spot_ref=now["spot"])
    return {"now": now, "before": before if before and before.get("found") else None}


def summarize(now: dict, before: dict | None) -> list[dict]:
    """Pure: per-greek value at spot + flip, now vs before."""
    p = now["profiles"]
    grid = [float(x) for x in p["spot_ref"]]
    spot = float(now["spot"])
    out = []
    for g in _GREEKS:
        if g not in p:
            continue
        cur = [float(v) for v in p[g]["all"]]
        row = {"greek": g, "at_spot": _at(grid, cur, spot), "flip": p[g].get("flip"),
               "at_spot_before": None, "flip_before": None}
        if before and g in before["profiles"]:
            prev = [float(v) for v in before["profiles"][g]["all"]]
            row["at_spot_before"] = _at(grid, prev, spot)
            row["flip_before"] = before["profiles"][g].get("flip")
        out.append(row)
    return out


def _f(v, fmt="{:+.2f}"):
    return "—" if v is None else fmt.format(v)


def _panel_html(sym: str, data: dict, svg) -> str:
    now, before = data["now"], data["before"]
    if not now.get("found"):
        return f'<h2>{sym}</h2><div class="mut">no per-strike book banked</div>'
    p = now["profiles"]
    grid = [float(x) for x in p["spot_ref"]]
    charts = []
    for g in _GREEKS:
        if g not in p:
            continue
        series = [(f"now ({now.get('as_of')})", _C1, [float(v) for v in p[g]["all"]])]
        if before and g in before["profiles"]:
            series.append((f"{before.get('as_of')} (prior)", _C2,
                           [float(v) for v in before["profiles"][g]["all"]]))
        chart = svg._line_svg(series, grid, w=520, h=190, zero=True, vline=float(now["spot"]),
                              x_fmt=lambda x: f"{x:,.0f}", y_fmt="{:.2f}")
        charts.append(f'<div><h3>{html.escape(_TITLES[g])}</h3><div class="panel">{chart}</div></div>')
    rows = "".join(
        f"<tr><td>{r['greek']}</td><td>{_f(r['at_spot'])}</td><td>{_f(r['at_spot_before'])}</td>"
        f"<td>{_f(r['flip'], '{:,.0f}')}</td><td>{_f(r['flip_before'], '{:,.0f}')}</td></tr>"
        for r in summarize(now, before))
    return (f'<h2>{sym} · spot {float(now["spot"]):,.2f}</h2>'
            '<table><tr><th>greek</th><th>@spot now</th><th>@spot prior</th><th>flip now</th>'
            f'<th>flip prior</th></tr>{rows}</table><div class="grid2">{"".join(charts)}</div>')


def render(payloads: dict) -> str:
    svg = _svg_mod()
    body = "".join(_panel_html(s, d, svg) for s, d in payloads.items())
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dealer Positioning</title><style>
body{{background:{_BG};color:{_TXT};font:14px/1.45 -apple-system,Segoe UI,Roboto,sans-serif;margin:0}}
.wrap{{max-width:1120px;margin:0 auto;padding:16px}} h1{{font-size:20px;margin:0 0 4px}}
h2{{font-size:15px;margin:22px 0 6px;border-bottom:1px solid #232833;padding-bottom:4px}} h3{{font-size:12px;color:{_MUT};margin:10px 0 4px}}
.grid2{{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:10px}} .panel{{background:{_PANEL};border-radius:6px;padding:8px}}
table{{border-collapse:collapse;font-size:12px;width:100%;max-width:560px}} th,td{{padding:3px 8px;text-align:right;border-bottom:1px solid #1d232c}} th:first-child,td:first-child{{text-align:left}}
.legend{{color:{_MUT};font-size:11px;margin-top:4px}} .k{{display:inline-block;width:10px;height:10px;border-radius:2px;margin:0 4px 0 10px;vertical-align:middle}}
.mut{{color:{_MUT}}} .foot{{color:{_MUT};font-size:11px;margin-top:18px}}
</style></head><body><div class="wrap">
<h1>DEALER POSITIONING · now vs {_WINDOW} sessions earlier</h1><div class="mut">built {date.today()} · both books priced on today's spot ladder</div>
{body}
<p class="foot">Calls+/puts− convention on the banked per-strike OI (index chains = 08:35 AM snapshot). An inferred dealer book, not cleared inventory — read the shape, zero-lines and how they moved, not the level. Gamma = $ delta per 1% move; charm = $ delta per day; vanna = $ delta per vol-pt; vega = $ per vol-pt; all $bn. Descriptor only (FlashAlpha rule 4).</p>
</div></body></html>"""


def build(symbols: tuple[str, ...] = _SYMS, *, out_path: str | None = None, settings=None,
          session=None) -> str:
    if session is not None:
        payloads = {s: _collect(session, s) for s in symbols}
    else:
        from trading_intel.config import get_settings
        from trading_intel.memory.db import make_session_factory

        settings = settings or get_settings()

        with make_session_factory(settings)() as sess:
            payloads = {s: _collect(sess, s) for s in symbols}
    out = (Path(out_path) if out_path else (Path(__file__).resolve().parents[1] / "reports") / f"dealer_positioning_{date.today()}.html").resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(payloads), encoding="utf-8")
    return str(out)


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="SPX/SPY dealer positioning, now vs earlier book.")
    ap.add_argument("--no-push", action="store_true")
    a = ap.parse_args()
    from trading_intel.config import get_settings

    st = get_settings()
    path = build(settings=st)
    if not a.no_push:
        from trading_intel.clients.telegram import TelegramClient

        TelegramClient(st).send_document(path, caption="Dealer positioning — SPX / SPY (now vs prior book)")
    print(f"dealer positioning: {path}")


if __name__ == "__main__":
    main()
