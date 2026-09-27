"""SPX Skew & Vanna report — normalized skew, wing skews, smile shift, dealer vanna.

Production version of the skew/vanna lab (VolSignals framework, see
``market.skew_vanna``). Reads banked ``vol_surface_cm`` (constant-maturity 30d rung)
for the skew panels and two ``get_profile`` books (latest vs ~5 sessions earlier,
same pinned spot grid) for the vanna panel. Server-side inline SVG, no <script>, so
it opens in Telegram / on a phone. Descriptor only (FlashAlpha rule 4).

Run:
    python scripts/skew_vanna_report.py            # build + push to Telegram
    python scripts/skew_vanna_report.py --no-push
"""

from __future__ import annotations

import html
from pathlib import Path

import structlog
from sqlalchemy import select

from trading_intel.market import skew_vanna as sv

log = structlog.get_logger(__name__)

_SYM = "SPX"
_RUNG = 30
_WINDOW = 5
_BG, _PANEL, _TXT, _MUT = "#0f1216", "#161b22", "#e6edf3", "#7a8699"
_C1, _C2, _C3, _C4 = "#e0a23a", "#4ea1ff", "#2fbf71", "#e0524a"
_LABEL_COLOR = {
    "skew-crushed-on-rally": _C3, "skew-crushed": _C3, "skew-bid-on-selloff": _C4,
    "skew-bid-into-rally": _C1, "skew-steady": _MUT, "no-data": _MUT,
}


# ── data ─────────────────────────────────────────────────────────────────────
def _collect(session, sym: str) -> dict:
    from trading_intel.mcp import profile_tool as pt
    from trading_intel.memory.models import OiChainEod, VolSurfaceCM
    from trading_intel.timeutils import is_trading_session

    rows = session.execute(
        select(VolSurfaceCM.ts, VolSurfaceCM.dte, VolSurfaceCM.delta, VolSurfaceCM.side,
               VolSurfaceCM.iv, VolSurfaceCM.spot)
        .where(VolSurfaceCM.symbol == sym, VolSurfaceCM.dte == _RUNG)
        .order_by(VolSurfaceCM.ts)
    ).all()
    dicts = [dict(ts=r.ts, dte=r.dte, delta=r.delta, side=r.side, iv=r.iv, spot=r.spot)
             for r in rows]
    sessions = {d["ts"] for d in dicts if is_trading_session(d["ts"])}
    series = sv.skew_series(dicts, dte=_RUNG, sessions=sessions)
    days = sorted(sessions)
    smile_now = [d for d in dicts if days and d["ts"] == days[-1]]
    smile_prev = [d for d in dicts if len(days) > _WINDOW and d["ts"] == days[-1 - _WINDOW]]

    now = pt.get_profile(session, sym, n_points=81)
    before = None
    if now.get("found"):
        ts_days = session.execute(
            select(OiChainEod.ts).where(OiChainEod.symbol == sym).distinct()
            .order_by(OiChainEod.ts.desc()).limit(_WINDOW + 1)
        ).scalars().all()
        if len(ts_days) > _WINDOW:
            before = pt.get_profile(session, sym, n_points=81, as_of=ts_days[-1].date(),
                                    spot_ref=now["spot"])
    return {"series": series, "read": sv.skew_read(series, window=_WINDOW),
            "smile_now": smile_now, "smile_prev": smile_prev,
            "vanna": sv.vanna_read(now, before)}


# ── svg helpers ──────────────────────────────────────────────────────────────
def _scale(v, lo, hi, a, b):
    return a if hi == lo else a + (v - lo) * (b - a) / (hi - lo)


def _line_svg(series: list[tuple[str, str, list]], xs: list, *, w=560, h=220, y_fmt="{:.2f}",
              zero=False, vline=None, x_fmt=None, right=None) -> str:
    """Multi-line chart. series=[(label,color,ys)], optional right=(label,color,ys) 2nd axis."""
    pad_l, pad_r, pad_t, pad_b = 46, 46 if right else 12, 12, 26
    ys_all = [y for _, _, ys in series for y in ys if y is not None]
    if zero:
        ys_all.append(0.0)
    if not ys_all or len(xs) < 2:
        return '<div class="mut">not enough history yet</div>'
    lo, hi = min(ys_all), max(ys_all)
    span = (hi - lo) or 1.0
    lo, hi = lo - 0.08 * span, hi + 0.08 * span
    xn = [_scale(i, 0, len(xs) - 1, pad_l, w - pad_r) for i in range(len(xs))] \
        if not isinstance(xs[0], (int, float)) else \
        [_scale(x, xs[0], xs[-1], pad_l, w - pad_r) for x in xs]
    out = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img">']
    for k in range(5):
        yv = lo + (hi - lo) * k / 4
        yy = _scale(yv, lo, hi, h - pad_b, pad_t)
        out.append(f'<line x1="{pad_l}" x2="{w-pad_r}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="#222a35"/>'
                   f'<text x="{pad_l-4}" y="{yy+4:.1f}" fill="{_MUT}" font-size="10" text-anchor="end">{y_fmt.format(yv)}</text>')
    if zero and lo < 0 < hi:
        yz = _scale(0, lo, hi, h - pad_b, pad_t)
        out.append(f'<line x1="{pad_l}" x2="{w-pad_r}" y1="{yz:.1f}" y2="{yz:.1f}" stroke="{_MUT}" stroke-dasharray="4 3"/>')
    for label, color, ys in series:
        pts = [f"{xn[i]:.1f},{_scale(y, lo, hi, h-pad_b, pad_t):.1f}" for i, y in enumerate(ys) if y is not None]
        dash = ' stroke-dasharray="5 4"' if label.endswith("(prior)") else ""
        out.append(f'<polyline fill="none" stroke="{color}" stroke-width="2"{dash} points="{" ".join(pts)}"/>')
    if right:
        _, rc, rys = right
        rv = [y for y in rys if y is not None]
        if rv:
            rlo, rhi = min(rv), max(rv)
            pts = [f"{xn[i]:.1f},{_scale(y, rlo, rhi, h-pad_b, pad_t):.1f}" for i, y in enumerate(rys) if y is not None]
            out.append(f'<polyline fill="none" stroke="{rc}" stroke-width="1.2" opacity="0.7" points="{" ".join(pts)}"/>')
            for yv in (rlo, rhi):
                yy = _scale(yv, rlo, rhi, h - pad_b, pad_t)
                out.append(f'<text x="{w-pad_r+4}" y="{yy+4:.1f}" fill="{rc}" font-size="10">{yv:,.0f}</text>')
    if vline is not None and isinstance(xs[0], (int, float)):
        xv = _scale(vline, xs[0], xs[-1], pad_l, w - pad_r)
        out.append(f'<line x1="{xv:.1f}" x2="{xv:.1f}" y1="{pad_t}" y2="{h-pad_b}" stroke="{_TXT}" stroke-dasharray="2 3"/>'
                   f'<text x="{xv+3:.1f}" y="{pad_t+10}" fill="{_TXT}" font-size="10">spot</text>')
    idx = [0, len(xs) // 2, len(xs) - 1]
    for i in idx:
        lab = x_fmt(xs[i]) if x_fmt else str(xs[i])
        out.append(f'<text x="{xn[i]:.1f}" y="{h-8}" fill="{_MUT}" font-size="10" text-anchor="middle">{html.escape(lab)}</text>')
    out.append("</svg>")
    leg = " ".join(f'<span class="k" style="background:{c}"></span>{html.escape(l)}' for l, c, _ in series)
    if right:
        leg += f' <span class="k" style="background:{right[1]}"></span>{html.escape(right[0])} (right axis)'
    return "".join(out) + f'<div class="legend">{leg}</div>'


def _smile(rows: list[dict]) -> tuple[list[str], list[float | None]]:
    """IV by delta: puts 10→50 then calls 45→10 (left = downside)."""
    order = [("put", d) for d in (10, 15, 20, 25, 30, 35, 40, 45)] + [("atm", 50)] + \
            [("call", d) for d in (45, 40, 35, 30, 25, 20, 15, 10)]
    look = {(str(r["side"]).lower(), float(r["delta"])): r["iv"] for r in rows}
    labs, ys = [], []
    for side, d in order:
        if side == "atm":
            vals = [look.get(("put", 50.0)), look.get(("call", 50.0))]
            vals = [v for v in vals if v is not None]
            ys.append(sum(vals) / len(vals) * 100 if vals else None)
            labs.append("ATM")
        else:
            v = look.get((side, float(d)))
            ys.append(v * 100 if v is not None else None)
            labs.append(f"{d}Δ{side[0].upper()}")
    return labs, ys


def _f(v, fmt="{:.2f}"):
    return "—" if v is None else fmt.format(v)


# ── render ───────────────────────────────────────────────────────────────────
def _render(data: dict, sym: str) -> str:
    s, rd, vr = data["series"], data["read"], data["vanna"]
    label = rd.get("label", "no-data")
    notes = list(vr.notes) if vr else []
    banner = {
        "skew-crushed-on-rally": "Skew CRUSHED on the rally — puts cheapened vs calls; dealers' short-skew shrinks mechanically.",
        "skew-crushed": "Skew crushed without a rally — wings cheapened; watch for complacency.",
        "skew-bid-on-selloff": "Skew BID on the selloff — put demand / fear confirming the move.",
        "skew-bid-into-rally": "Skew bid INTO a rally — hedging demand rising under a rising market (caution).",
        "skew-steady": "Skew steady — no re-mark worth calling.",
        "no-data": "Not enough banked surface history yet.",
    }[label]
    if vr and vr.inverted:
        banner += " VANNA INVERSION — dealer vanna at spot flipped sign vs the earlier book (spot-up/vol-up risk)."
    xs = [p.d for p in s]
    fmt_d = lambda d: f"{d:%-m/%-d}"  # noqa: E731
    skew_chart = _line_svg([("normalized skew (25Δp−25Δc)/ATM", _C1, [p.norm_skew for p in s])],
                           xs, x_fmt=fmt_d, right=("SPX", _MUT, [p.spot for p in s]))
    wing_chart = _line_svg([("put skew 25Δp−ATM", _C4, [p.put_skew for p in s]),
                            ("call skew 25Δc−ATM", _C3, [p.call_skew for p in s])],
                           xs, x_fmt=fmt_d, y_fmt="{:+.1f}", zero=True)
    labs, now_y = _smile(data["smile_now"])
    _, prev_y = _smile(data["smile_prev"])
    smile_series = [("30d smile now", _C2, now_y)]
    if any(v is not None for v in prev_y):
        smile_series.append((f"{_WINDOW} sessions ago (prior)", _MUT, prev_y))
    smile_chart = _line_svg(smile_series, labs, y_fmt="{:.1f}")
    if vr:
        vs = [(f"dealer vanna now ({vr.as_of_now})", _C1, vr.now)]
        if vr.before:
            vs.append((f"{vr.as_of_before} (prior)", _MUT, vr.before))
        vanna_chart = _line_svg(vs, vr.grid, x_fmt=lambda x: f"{x:,.0f}", zero=True,
                                vline=vr.spot, y_fmt="{:.0f}")
    else:
        vanna_chart = '<div class="mut">no per-strike book</div>'
    last = s[-1] if s else None
    kpis = [
        ("Normalized skew 30d", _f(rd.get("norm_skew"), "{:.3f}"),
         f"pctile {_f((rd.get('pctile') or 0)*100, '{:.0f}')} of {rd.get('n', 0)} sessions"),
        (f"Δ skew {_WINDOW}d", _f(rd.get("d_skew"), "{:+.3f}"),
         f"SPX {_f((rd.get('d_spot') or 0)*100, '{:+.1f}')}%"),
        ("Put skew / call skew", f"{_f(last.put_skew if last else None, '{:+.1f}')} / {_f(last.call_skew if last else None, '{:+.1f}')}", "vol pts vs ATM"),
        ("ATM IV 30d", _f((last.atm or 0)*100 if last and last.atm else None, "{:.1f}"), "constant-maturity"),
        ("Vanna @ spot", _f(vr.at_spot_now if vr else None, "{:+.1f}"),
         f"prior {_f(vr.at_spot_before if vr else None, '{:+.1f}')} ($bn, calls+/puts−)"),
        ("Vanna zero-line", _f(vr.zero_now if vr else None, "{:,.0f}"),
         f"prior {_f(vr.zero_before if vr else None, '{:,.0f}')}"),
    ]
    kpi_html = "".join(f'<div class="kpi"><div class="kl">{html.escape(a)}</div><div class="kv">{b}</div>'
                       f'<div class="ks">{html.escape(c)}</div></div>' for a, b, c in kpis)
    note_html = "".join(f"<li>{html.escape(n)}</li>" for n in notes)
    col = _LABEL_COLOR.get(label, _MUT)
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{sym} Skew &amp; Vanna</title><style>
body{{background:{_BG};color:{_TXT};font:14px/1.45 -apple-system,Segoe UI,Roboto,sans-serif;margin:0}}
.wrap{{max-width:1180px;margin:0 auto;padding:18px}} h1{{font-size:20px;margin:0 0 4px}} h2{{font-size:14px;color:{_MUT};margin:18px 0 6px;text-transform:uppercase;letter-spacing:.04em}}
.read{{border-left:4px solid {col};background:{_PANEL};padding:10px 14px;border-radius:6px;margin:12px 0}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:8px}}
.kpi{{background:{_PANEL};border-radius:6px;padding:10px}} .kl{{color:{_MUT};font-size:11px}} .kv{{font-size:20px;font-weight:700}} .ks{{color:{_MUT};font-size:11px}}
.grid2{{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:12px}} .panel{{background:{_PANEL};border-radius:6px;padding:10px}}
.legend{{color:{_MUT};font-size:11px;margin-top:4px}} .k{{display:inline-block;width:10px;height:10px;border-radius:2px;margin:0 4px 0 10px;vertical-align:middle}}
.mut{{color:{_MUT}}} .foot{{color:{_MUT};font-size:11px;margin-top:18px}}
</style></head><body><div class="wrap">
<h1>{sym} · SKEW &amp; VANNA</h1><div class="mut">30d constant-maturity rung · window {_WINDOW} sessions · as of {rd.get('as_of', '—')}</div>
<div class="read"><b>READ · {html.escape(label)}</b><br>{html.escape(banner)}{f'<ul>{note_html}</ul>' if note_html else ''}</div>
<div class="kpis">{kpi_html}</div>
<div class="grid2">
<div><h2>Normalized skew vs SPX</h2><div class="panel">{skew_chart}</div></div>
<div><h2>Wing skews (vol pts vs ATM)</h2><div class="panel">{wing_chart}</div></div>
<div><h2>30d smile · now vs {_WINDOW} sessions ago</h2><div class="panel">{smile_chart}</div></div>
<div><h2>Dealer vanna by spot · now vs earlier book</h2><div class="panel">{vanna_chart}</div></div>
</div>
<p class="foot">Normalized skew = (25Δ put IV − 25Δ call IV) / ATM IV (1M SPX usually ~0.20–0.50). Vanna from the per-strike EOD book under the calls+/puts− convention — an inferred dealer book, not cleared inventory: read the SHAPE and the zero-line, not the level. Both vanna curves use today's spot grid. Vega / ES-per-vol-point not yet computed. Descriptor only (FlashAlpha rule 4).</p>
</div></body></html>"""


def build(symbol: str = _SYM, *, out_path: str | None = None, settings: object = None,
          session: object = None) -> str:
    from trading_intel.config import get_settings

    settings = settings or get_settings()
    sym = symbol.strip().upper()
    if session is not None:
        data = _collect(session, sym)
    else:
        from trading_intel.memory.db import make_session_factory

        with make_session_factory(settings)() as s:
            data = _collect(s, sym)
    out = (Path(out_path) if out_path else Path("reports") / f"skew_vanna_{sym}.html").resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_render(data, sym), encoding="utf-8")
    return str(out)


def main() -> None:
    import argparse

    p = argparse.ArgumentParser(description="Build the SPX skew & vanna report.")
    p.add_argument("symbol", nargs="?", default=_SYM)
    p.add_argument("--no-push", action="store_true")
    p.add_argument("--out", default=None)
    a = p.parse_args()
    from trading_intel.config import get_settings

    settings = get_settings()
    path = build(a.symbol, out_path=a.out, settings=settings)
    print(f"skew_vanna report: {path}")
    if not a.no_push:
        from trading_intel.clients.telegram import TelegramClient

        print("telegram_sent=",
              TelegramClient(settings).send_document(path, caption=f"{a.symbol.upper()} Skew & Vanna"))


if __name__ == "__main__":
    main()
