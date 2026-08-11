"""GEX-Transition Signal report — SPX dealer-gamma "quiet unwind" state → one
self-contained HTML, pushed to Telegram.

Canonical generator + CLI. Mirrors the other ``scripts/*_report.py``: the layout
lives here once (inlined HTML/SVG string), and ``trading_intel.reports.
build_gex_transition`` loads this module's ``build()``.

Takes the cReserve / "Daily GEX Print" backtest as GIVEN — this only *reads
today's state* from the pure state machine in ``market.gex_transition`` (net GEX
EOD via ``get_gamma_history``, CLEAN ATM IV via ``get_iv_tenor``). Descriptor /
research track only (FlashAlpha rule 4) — no signal is emitted.

PHONE RULE: rendered ENTIRELY server-side — every element is a static HTML/SVG
string, no <script>, no CDN — so it opens in Telegram's in-app viewer.

Run:
    python scripts/gex_transition_report.py            # build + push to Telegram
    python scripts/gex_transition_report.py --no-push  # build only
"""

from __future__ import annotations

import html as _html
from pathlib import Path

import structlog

from trading_intel.market.gex_transition import (
    STATE_CONFIRMED,
    STATE_DROP,
    STATE_QUIET,
    STATE_REBUILD,
    compute,
)

log = structlog.get_logger(__name__)

_SYMBOL = "SPX"
_TENOR = 30
_WINDOW = 14  # sessions shown in the strip / charts
_DEFAULT_OUT = Path("reports") / "gex_transition.html"

_BLUE = "#2f6df0"
_RED = "#d1495b"
_AMBER = "#e08a1e"
_GREEN = "#2f9e6f"
_GREY = "#93a0b3"
_MUT = "#9aa3b2"

_STATE_META = {
    STATE_QUIET: ("QUIET UNWIND", _RED),
    STATE_CONFIRMED: ("CONFIRMED DROP", _AMBER),
    STATE_DROP: ("GEX DROP", _AMBER),
    STATE_REBUILD: ("REBUILD", _GREEN),
    "base": ("BASE", _GREY),
}


def _meta(state: str) -> tuple[str, str]:
    return _STATE_META.get(state, ("BASE", _GREY))


# ── data ─────────────────────────────────────────────────────────────────────
def _collect(session) -> object:
    from trading_intel.mcp.extra_tools import get_iv_tenor
    from trading_intel.mcp.tools import get_gamma_history

    gamma = get_gamma_history(session, _SYMBOL, days=120).get("rows") or []
    iv = get_iv_tenor(session, symbols=[_SYMBOL], tenor_dte=_TENOR, days=120).get("rows") or []
    return compute(gamma, iv, tenor_dte=_TENOR)


# ── svg helpers (server-side) ────────────────────────────────────────────────
def _line_svg(dates, vals, color, *, zero=False, fmt="{:.0f}", unit="", h=120):
    pts = [(d, v) for d, v in zip(dates, vals) if v is not None]
    if len(pts) < 2:
        return '<div style="color:#9aa3b2;font-size:12px">insufficient data</div>'
    dd = [p[0] for p in pts]
    vv = [p[1] for p in pts]
    w, pl, pr, pt, pb = 680, 46, 14, 14, 26
    base = [0.0] if zero else []
    lo, hi = min(vv + base), max(vv + base)
    rng = (hi - lo) or 1.0
    X = lambda i: pl + i * (w - pl - pr) / (len(vv) - 1)
    Y = lambda v: pt + (hi - v) / rng * (h - pt - pb)
    path = "M" + " L".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(vv))
    s = [f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}" xmlns="http://www.w3.org/2000/svg" '
         f'font-family="ui-sans-serif,system-ui,sans-serif">']
    if zero:
        s.append(f'<line x1="{pl}" y1="{Y(0):.1f}" x2="{w-pr}" y2="{Y(0):.1f}" '
                 f'stroke="#cbd2dd" stroke-dasharray="3 4"/>')
        s.append(f'<text x="{w-pr}" y="{Y(0)-4:.1f}" fill="{_MUT}" font-size="9" '
                 f'text-anchor="end">0 · short&#8596;long</text>')
    for v in (hi, lo):
        s.append(f'<text x="{pl-6}" y="{Y(v)+3:.1f}" fill="{_MUT}" font-size="9.5" '
                 f'text-anchor="end">{fmt.format(v)}{unit}</text>')
    s.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2.2" stroke-linejoin="round"/>')
    for i, v in enumerate(vv):
        s.append(f'<circle cx="{X(i):.1f}" cy="{Y(v):.1f}" r="2.4" fill="{color}"/>')
    s.append(f'<circle cx="{X(len(vv)-1):.1f}" cy="{Y(vv[-1]):.1f}" r="4" fill="{color}" '
             f'stroke="#fff" stroke-width="1.5"/>')
    for i in (0, len(dd) // 2, len(dd) - 1):
        s.append(f'<text x="{X(i):.1f}" y="{h-8}" fill="{_MUT}" font-size="9" '
                 f'text-anchor="middle">{dd[i]}</text>')
    s.append("</svg>")
    return "".join(s)


def _fmt_pct_over(spot, flip):
    if spot and flip:
        return f"+{(spot/flip-1)*100:.1f}% over flip" if spot >= flip else f"{(spot/flip-1)*100:.1f}% vs flip"
    return "—"


# ── render ───────────────────────────────────────────────────────────────────
def _render_html(res) -> str:
    rows = [r for r in res.rows if r.net_gex is not None]
    win = rows[-_WINDOW:] if len(rows) > _WINDOW else rows
    cur = res.latest
    if cur is None:
        return "<html><body><p>No GEX-transition data.</p></body></html>"
    cur_label, cur_col = _meta(cur.state)
    asof = cur.date.isoformat()

    cz = f"{cur.d_gex_z:+.2f}&#963;" if cur.d_gex_z is not None else "&#8212;"
    cdiv = f"{cur.d_iv_pt:+.2f} pt" if cur.d_iv_pt is not None else "&#8212;"
    civ = f"{cur.atm_iv:.1f}%" if cur.atm_iv is not None else "&#8212;"
    over = _fmt_pct_over(cur.spot, cur.flip)
    flip_s = f"{cur.flip:.0f}" if cur.flip else "&#8212;"
    spot_s = f"{cur.spot:.0f}" if cur.spot else "&#8212;"

    dates = [r.date.strftime("%m-%d") for r in win]
    gex_line = _line_svg(dates, [r.net_gex for r in win], _BLUE, zero=True, fmt="{:.0f}")
    iv_line = _line_svg(dates, [r.atm_iv for r in win], _AMBER, fmt="{:.1f}", unit="%")

    cells = []
    for r in win:
        _, c = _meta(r.state)
        zt = f"{r.d_gex_z:+.1f}&#963;" if r.d_gex_z is not None else "&#8212;"
        cells.append(
            f'<div class="cell" style="border-top-color:{c}"><div class="cd">{r.date.strftime("%m-%d")}</div>'
            f'<div class="cg" style="color:{c}">{r.net_gex:.0f}</div><div class="cz">{zt}</div></div>'
        )
    strip = "".join(cells)

    firing = cur.state in (STATE_QUIET, STATE_CONFIRMED, STATE_DROP)
    if cur.state == STATE_QUIET:
        explain = ("<b>QUIET UNWIND firing.</b> Net-GEX dropped fast while IV stayed pinned — "
                   "support left before anyone bid protection. Per the taken-as-given backtest, "
                   "this state leans bearish / de-risk.")
    elif firing:
        explain = ("<b>GEX drop, IV moved.</b> A fast gamma drop with vol confirming (or ambiguous) — "
                   "closer to the base rate than the dangerous quiet unwind.")
    else:
        explain = ("<b>Nothing firing.</b> GEX is roughly flat day-over-day and IV is steady — the "
                   "base state. The signal to watch is a fast net-GEX drop (&#916;GEX &#8804; &#8722;1.5&#963;) "
                   "while IV stays pinned (|&#916;IV| &#8804; 0.5pt) — the &quot;quiet unwind.&quot;")

    mu = f"{res.mu:.0f}" if res.mu is not None else "&#8212;"
    sigma = f"{res.sigma:.0f}" if res.sigma is not None else "&#8212;"

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>SPX GEX-Transition Signal &#8212; {asof}</title>
<style>
:root{{--bg:#f6f7f9;--card:#fff;--ink:#1f2733;--mut:#6b7686;--line:#e6e9ef;--blue:#2f6df0;--red:#d1495b;--amber:#e08a1e;--green:#2f9e6f}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;line-height:1.55;font-size:15px}}
.wrap{{max-width:900px;margin:0 auto;padding:30px 20px 70px}}
.eyebrow{{color:var(--blue);font-weight:700;letter-spacing:.12em;text-transform:uppercase;font-size:11px}}
h1{{font-size:24px;margin:.3em 0 .1em}}.sub{{color:var(--mut);margin:.2em 0 0;font-size:14px}}
h2{{font-size:15px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);margin:2em 0 .6em;font-weight:700}}
.state{{display:flex;align-items:center;gap:16px;background:var(--card);border:1px solid var(--line);border-left:5px solid {cur_col};border-radius:12px;padding:16px 18px;margin:16px 0;box-shadow:0 1px 2px rgba(20,30,50,.03)}}
.state .big{{font-size:25px;font-weight:800;color:{cur_col}}}.state .meta{{color:var(--mut);font-size:13px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:15px 17px;margin:11px 0;box-shadow:0 1px 2px rgba(20,30,50,.03)}}
table{{width:100%;border-collapse:collapse;font-size:13.5px}}th,td{{text-align:left;padding:8px;border-bottom:1px solid var(--line)}}
td.num{{text-align:right;font-variant-numeric:tabular-nums}}thead th{{font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--mut)}}tbody tr:last-child td{{border-bottom:none}}
.strip{{display:grid;grid-template-columns:repeat({len(win)},1fr);gap:5px;margin:6px 0}}
.cell{{border:1px solid var(--line);border-top-width:3px;border-radius:7px;padding:6px 3px;text-align:center;background:#fff}}
.cell .cd{{font-size:9px;color:var(--mut)}}.cell .cg{{font-size:12.5px;font-weight:800}}.cell .cz{{font-size:8.5px;color:var(--mut)}}
.rule{{background:#eef3ff;border:1px solid #d5e0fb;border-radius:10px;padding:11px 14px;margin:9px 0}}
.explain{{border-left:3px solid var(--amber);background:#fff8ee;border-radius:0 10px 10px 0;padding:11px 14px;margin:11px 0;font-size:13.5px}}
.chartlbl{{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);margin:2px 0}}
.leg{{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:var(--mut);margin-top:6px}}
.dot{{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:5px;vertical-align:-1px}}
ul{{margin:.4em 0;padding-left:1.15em}}li{{margin:.28em 0}}b{{color:#141b26}}
code{{background:#eef1f6;border-radius:5px;padding:1px 6px;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12.5px}}
.foot{{color:var(--mut);font-size:12px;border-top:1px solid var(--line);margin-top:26px;padding-top:13px}}
</style></head><body><div class="wrap">
<div class="eyebrow">Trading-Intel &#183; Dealer-Gamma Signal</div>
<h1>SPX GEX-Transition Signal</h1>
<p class="sub">As of {asof} EOD &#183; live &quot;quiet unwind&quot; state detector (backtest taken as given &#8212; this reads the state, it does not re-test the edge).</p>
<div class="state">
  <div><div class="big">{cur_label}</div><div class="meta">net GEX {cur.net_gex:.0f} &#183; {over}</div></div>
  <div style="margin-left:auto;text-align:right">
    <div class="meta">&#916;GEX <b style="color:#141b26">{cz}</b> &#183; &#916;IV <b style="color:#141b26">{cdiv}</b></div>
    <div class="meta">ATM IV {civ} &#183; flip {flip_s} &#183; spot {spot_s}</div>
  </div>
</div>
<div class="explain">{explain}</div>
<h2>The rule (from the trusted backtest)</h2>
<div class="card"><div class="rule"><b>IF</b> net-GEX drops fast <b>AND</b> IV unmoved &#8594; quiet unwind &#8594; <b style="color:var(--red)">lean bearish / de-risk</b>. &nbsp;<b>IF</b> GEX drops <b>AND</b> IV rises &#8594; confirmed &#8594; base rate. &nbsp;Slow bleed = noise.</div></div>
<h2>Net GEX &amp; ATM IV &#8212; last {len(win)} sessions</h2>
<div class="card">
  <div class="chartlbl" style="color:var(--blue)">Net GEX (dealer gamma)</div>{gex_line}
  <div class="chartlbl" style="color:var(--amber);margin-top:10px">ATM IV &#183; 1M (iv_tenor, clean EOD)</div>{iv_line}
  <p class="sub" style="margin-bottom:0">The signal fires only when the <b>blue line drops hard</b> (&#916;GEX &#8804; &#8722;1.5&#963;) <b>while amber stays flat</b>.</p>
</div>
<h2>State strip</h2>
<div class="card"><div class="strip">{strip}</div>
  <div class="leg"><span><span class="dot" style="background:{_RED}"></span>quiet unwind</span>
  <span><span class="dot" style="background:{_AMBER}"></span>gex drop / confirmed</span>
  <span><span class="dot" style="background:{_GREY}"></span>base</span>
  <span><span class="dot" style="background:{_GREEN}"></span>rebuild</span></div></div>
<h2>How today is computed</h2>
<div class="card"><table>
<thead><tr><th>Input</th><th class="num">Value</th><th>Source</th></tr></thead>
<tbody>
<tr><td>Net GEX (EOD)</td><td class="num">{cur.net_gex:.0f}</td><td><code>gamma_history</code></td></tr>
<tr><td>&#916;GEX z-score</td><td class="num">{cz}</td><td>vs trailing &#916;GEX (n={res.n_changes}, &#956;={mu}, &#963;={sigma})</td></tr>
<tr><td>ATM IV / &#916;IV (1M)</td><td class="num">{civ} / {cdiv}</td><td><b><code>iv_tenor</code></b> (clean constant-maturity)</td></tr>
</tbody></table></div>
<p class="foot">Sources &#8212; <code>get_gamma_history(SPX)</code> + <code>get_iv_tenor(SPX,30d)</code>. Unit-free trigger: quiet &#916;GEX&#8804;&#8722;1.5&#963; &amp; |&#916;IV|&#8804;0.5pt; confirmed &#916;GEX&#8804;&#8722;1.5&#963; &amp; &#916;IV&#8805;+1pt. Forward-return behavior taken from the external backtest, not recomputed. Descriptor only (rule 4).</p>
</div></body></html>"""


def build(*, out_path: str | None = None, settings: object = None, session: object = None) -> str:
    """Render the GEX-transition report to one HTML file; return absolute path."""
    from trading_intel.config import get_settings

    settings = settings or get_settings()
    if session is not None:
        res = _collect(session)
    else:
        from trading_intel.memory.db import make_session_factory

        with make_session_factory(settings)() as s:
            res = _collect(s)
    html = _render_html(res)
    out = (Path(out_path) if out_path else _DEFAULT_OUT).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return str(out)


def main() -> None:
    import argparse

    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    parser = argparse.ArgumentParser(description="Build the SPX GEX-Transition Signal report.")
    parser.add_argument("--no-push", action="store_true", help="build only; do not push to Telegram")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    from trading_intel.config import get_settings

    settings = get_settings()
    path = build(out_path=args.out, settings=settings)
    print(f"gex_transition report: {path}")
    if not args.no_push:
        from trading_intel.clients.telegram import TelegramClient

        sent = TelegramClient(settings).send_document(path, caption="SPX GEX-Transition Signal")
        print(f"telegram_sent={sent}")


if __name__ == "__main__":
    main()
