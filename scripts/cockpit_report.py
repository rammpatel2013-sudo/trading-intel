"""Cockpit report — SPX + SPY dealer positioning → one self-contained HTML,
pushed to Telegram.

Canonical generator + CLI for the dealer-positioning cockpit (see MEMORY
``cockpit-report``). Mirrors the other ``scripts/*_report.py`` generators: the
layout lives here once, the HTML template is INLINED (a module string).
``trading_intel.reports.build_cockpit`` loads this module's ``build()`` so the
MCP ``generate_cockpit_report`` tool produces the identical file.

PHONE RULE (report-deploy-workflow): rendered ENTIRELY SERVER-SIDE — every card
is a static HTML/SVG string emitted by Python, there is NO client-side <script>
and NO CDN, so it opens in Telegram's in-app phone viewer. The SPX/SPY/QQQ
toggle is a pure-CSS radio/label tab (works with JS disabled). Reads the
Convex-fed DB via ``api.positioning.build_positioning`` — ZERO added vendor
calls. Descriptor only (FlashAlpha rule 4).

Run:
    python scripts/cockpit_report.py            # build + push to Telegram
    python scripts/cockpit_report.py --no-push  # build only
"""
from __future__ import annotations

import html as _html
import math
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

_SYMBOLS: tuple[str, ...] = ("SPX", "SPY", "QQQ")  # fallback; real default = config INDEX_ROOTS
_DEFAULT_OUT = Path("reports") / "cockpit.html"


# ── number formatting (server-side ports of the old JS helpers) ──────────────
def _finite(x: object) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(x)


def _fmtc(x: object, d: int = 2) -> str:
    return f"{float(x):,.{d}f}" if _finite(x) else "n/a"


def _abbr(x: object, d: int = 1) -> str:
    if not _finite(x):
        return "n/a"
    x = float(x)
    s = "−" if x < 0 else ""
    a = abs(x)
    if a >= 1e9:
        return f"{s}{a / 1e9:.{d}f}B"
    if a >= 1e6:
        return f"{s}{a / 1e6:.{d}f}M"
    if a >= 1e3:
        return f"{s}{a / 1e3:.{d}f}K"
    return f"{s}{a:.{d}f}"


def _pct(x: object, d: int = 2) -> str:
    return f"{float(x) * 100:.{d}f}%" if _finite(x) else "n/a"


def _signpct(x: object, d: int = 2) -> str:
    if not _finite(x):
        return "n/a"
    x = float(x)
    return ("+" if x >= 0 else "−") + f"{abs(x) * 100:.{d}f}%"


def _vols(x: object, d: int = 2) -> str | None:
    if not _finite(x):
        return None
    x = float(x)
    return ("+" if x >= 0 else "−") + f"{abs(x) * 100:.{d}f}"


def _g(obj: object, key: str, default: object = None) -> object:
    return obj.get(key, default) if isinstance(obj, dict) else default


# ── card renderers (server-side ports of the JS card functions) ──────────────
def _regime_card(p: dict) -> str:
    r = _g(p, "regime", {}) or {}
    short = _g(r, "amplifying")
    label = str(_g(r, "label") or "regime n/a").upper()
    col = "var(--mut)" if short is None else ("var(--red)" if short else "var(--grn)")
    pill = (
        ""
        if short is None
        else (
            '<div class="pill red">▼ BELOW FLIP</div>'
            if short
            else '<div class="pill grn">▲ ABOVE FLIP</div>'
        )
    )
    sub = "" if short is None else (
        "dealers amplify the move · spot below flip"
        if short
        else "dealers dampen the move · spot above flip"
    )
    edge = "rgba(255,93,106,.28)" if short else "rgba(47,224,166,.24)"
    return (
        f'<div class="card regime" style="border-color:{edge}">'
        f'<div class="row1"><div>'
        f'<div class="big" style="color:{col}">{_html.escape(label)}</div>'
        f'<div class="sub">{sub}</div></div>{pill}</div>'
        f'<div class="meta">'
        f'<div>SPOT<b>{_fmtc(_g(p, "spot"), 2)}</b></div>'
        f'<div>GAMMA FLIP<b>{_fmtc(_g(r, "gex_flip"), 2)}</b></div>'
        f'<div>DIST TO FLIP<b style="color:{col}">{_signpct(_g(r, "dist_to_flip"))}</b></div>'
        f"</div></div>"
    )


def _em_card(p: dict) -> str:
    e = _g(p, "expected_move")
    if not e:
        return ""
    lower, upper, spot = _g(e, "lower"), _g(e, "upper"), _g(p, "spot")
    mk = 50.0
    if _finite(lower) and _finite(upper) and _finite(spot) and upper > lower:
        mk = max(0.0, min(100.0, (float(spot) - float(lower)) / (float(upper) - float(lower)) * 100))
    dte = _g(e, "dte")
    dte_lbl = "0DTE" if dte == 0 else f"{dte}-day"
    return (
        '<div class="card emv">'
        f'<div class="lbl">Expected move · {dte_lbl}</div>'
        f'<div class="r"><div class="pct num">{_pct(_g(e, "pct"))}</div>'
        f'<div class="dol">±$${_fmtc(_g(e, "dollar"), 2)}</div></div>'.replace("$$", "$")
        + f'<div class="track"><div class="mk" style="left:{mk:.1f}%"></div></div>'
        '<div class="ends">'
        f'<div class="lo">MIN<b>{_fmtc(lower, 2)}</b></div>'
        f'<div class="sp">SPOT<b>{_fmtc(spot, 2)}</b></div>'
        f'<div class="hi">MAX<b>{_fmtc(upper, 2)}</b></div></div>'
        f'<div class="fine">ATM straddle · strike {_fmtc(_g(e, "atm_strike"), 0)} '
        f'· ATM IV {_pct(_g(e, "atm_iv"), 1)}</div></div>'
    )


def _dp_card(p: dict) -> str:
    g = _g(p, "gex", {}) or {}
    d = _g(p, "dex", {}) or {}
    by_dte = _g(g, "by_dte", []) or []
    bmax = max([abs(float(_g(b, "gex", 0) or 0)) for b in by_dte] + [1e-9])
    bars = []
    for b in by_dte:
        gv = float(_g(b, "gex", 0) or 0)
        w = abs(gv) / bmax * 50
        neg = gv < 0
        fill = "right:50%;background:var(--red)" if neg else "left:50%;background:var(--grn)"
        bars.append(
            f'<div class="bar"><div class="nm">{_html.escape(str(_g(b, "bucket", "")))} DTE</div>'
            f'<div class="tr"><div class="fill" style="{fill};width:{w:.1f}%"></div></div>'
            f'<div class="vl {"red" if neg else "grn"}">{_abbr(gv)}</div></div>'
        )
    gtot = _g(g, "total") or 0
    dtot = _g(d, "total") or 0
    gcol = "red" if gtot < 0 else "grn"
    dcol = "red" if dtot < 0 else "grn"
    flip = _g(d, "flip")
    if flip is None:
        flip_txt = '<span style="color:var(--amb)">pending persist</span>'
    else:
        flip_txt = (
            f'{_fmtc(flip, 2)} <span style="color:var(--mut)">'
            f'({_html.escape(str(_g(d, "side") or ""))} {_signpct(_g(d, "dist_to_flip"))})</span>'
        )
    return (
        '<div class="card"><div class="lbl">Dealer positioning</div>'
        '<div class="two">'
        '<div><div class="k">Net GEX <span style="color:#3a4448">· term</span></div>'
        f'<div class="v {gcol} num">{_abbr(_g(g, "total"))}</div>'
        f'<div class="u {"r" if gcol == "red" else "g"}">'
        f'{"short gamma" if gcol == "red" else "long gamma"} · near {_abbr(_g(g, "near_tenor"))}</div></div>'
        '<div><div class="k">Net DEX</div>'
        f'<div class="v {dcol} num">{_abbr(_g(d, "total"))}</div>'
        f'<div class="u {"r" if dcol == "red" else "g"}">{_html.escape(str(_g(d, "lean") or ""))}</div></div>'
        "</div>"
        '<div class="brk"><div class="h"><div class="t">GEX BREAKDOWN BY DTE</div>'
        f'<div class="tot">term total <b style="color:var(--{gcol})">{_abbr(_g(g, "total"))}</b></div></div>'
        f'{"".join(bars)}</div>'
        '<div class="lean"><div class="lt">Delta flip <span style="color:var(--mut)">(zero-DEX)</span>:</div>'
        f'<div class="rt">{flip_txt}</div></div></div>'
    )


def _flow_card(p: dict) -> str:
    f = _g(p, "flow", {}) or {}
    if _g(f, "pending"):
        return (
            '<div class="card"><div style="display:flex;justify-content:space-between;align-items:center">'
            '<div class="lbl">Put / Call volume</div>'
            '<div style="font-size:11px;color:var(--amb);font-weight:700;letter-spacing:.5px">PENDING</div></div>'
            '<div style="font-size:11px;color:var(--mut);margin-top:9px;line-height:1.5">Fills once '
            "<code>intraday_flow</code> is re-enabled for this index (set <code>INTRADAY_SYMBOLS</code>). "
            "Everything above is live from the Convex-fed DB.</div></div>"
        )
    cv = float(_g(f, "call_volume", 0) or 0)
    pv = float(_g(f, "put_volume", 0) or 0)
    tot = (cv + pv) or 1
    cf = cv / tot * 100
    pcr = _g(f, "pc_ratio")
    return (
        '<div class="card"><div class="lbl">Put / Call volume · live</div>'
        '<div style="text-align:center;margin:10px 0 2px">'
        f'<span class="num" style="font-size:22px;font-weight:700">{_fmtc(pcr, 2)}</span>'
        '<span style="font-size:12px;color:var(--mut)"> P/C</span></div>'
        f'<div class="pcbar"><div class="cf" style="width:{cf:.1f}%"></div>'
        f'<div class="pf" style="width:{100 - cf:.1f}%"></div></div>'
        f'<div class="pcrow"><div class="c">CALLS <b>{_abbr(cv, 0)}</b></div>'
        f'<div class="p"><b>{_abbr(pv, 0)}</b> PUTS</div></div>'
        '<div class="brk"><div class="h"><div class="t">TRADED Δ-NOTIONAL</div></div>'
        '<div class="pcrow" style="margin-top:2px">'
        f'<div class="c">calls <b>{_abbr(_g(f, "call_notional"))}</b></div>'
        f'<div class="p">puts <b>{_abbr(_g(f, "put_notional"))}</b></div></div></div></div>'
    )


def _skew_card(p: dict) -> str:
    s = _g(p, "skew", {}) or {}

    def cell(kk: str, v: object, ss: str) -> str:
        t = _vols(v)
        cls = "na" if t is None else ("red" if _finite(v) and float(v) >= 0 else "grn")
        return (
            f'<div class="cell"><div class="kk">{kk}</div>'
            f'<div class="vv {cls}">{"n/a" if t is None else t}</div>'
            f'<div class="ss">{ss}</div></div>'
        )

    return (
        '<div class="card sk"><div class="lbl">Skew · 25Δ risk-reversal (put − call, vols)</div>'
        '<div class="grid">'
        f'{cell("0DTE RR25", _g(s, "rr25_0dte"), "put bid")}'
        f'{cell("30D RR25", _g(s, "rr25_30d"), "put bid")}'
        f'{cell("30D RR10", _g(s, "rr10_30d"), "tails")}'
        "</div>"
        f'<div class="fine">ATM IV {_pct(_g(s, "atm_iv"), 1)} · positive = downside puts richer (fear)</div></div>'
    )


def _panel(p: dict) -> str:
    """Full set of cards + a footer meta line for ONE symbol."""
    meta = _g(p, "meta", {}) or {}
    as_of = str(_g(p, "as_of") or "").replace("T", " ")
    footmeta = (
        f'{_html.escape(str(_g(p, "symbol", "")))} · {_g(meta, "n_contracts", "?")} contracts '
        f'· as of {_html.escape(as_of)} · source {_html.escape(str(_g(meta, "source", "")))}'
    )
    return (
        _regime_card(p)
        + _em_card(p)
        + _dp_card(p)
        + _flow_card(p)
        + _skew_card(p)
        + f'<p class="pmeta">{footmeta}</p>'
    )


_STYLE = r"""<style>
  :root{
    --bg:#08090a; --card:#111618; --card2:#0d1214; --edge:#1c2427;
    --grn:#2fe0a6; --grn-dim:#1c8e6c; --red:#ff5d6a; --red-dim:#9e3540;
    --amb:#f4b942; --txt:#e9eef0; --mut:#6c777d;
    --mono:"SF Mono",ui-monospace,"Roboto Mono",Menlo,monospace;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--bg);color:var(--txt);
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
    -webkit-font-smoothing:antialiased;display:flex;justify-content:center;padding:16px 12px 40px}
  .app{width:100%;max-width:404px}
  .num{font-family:var(--mono);font-variant-numeric:tabular-nums;letter-spacing:-.02em}
  .tgr{position:absolute;opacity:0;pointer-events:none}

  .top{display:flex;align-items:center;justify-content:space-between;padding:4px 4px 12px}
  .toggle{display:flex;gap:6px}
  .toggle label{background:#12181a;border:1px solid var(--edge);color:var(--mut);
    font:600 13px/1 -apple-system,sans-serif;letter-spacing:1px;padding:8px 14px;border-radius:10px;cursor:pointer}
  .status{display:flex;align-items:center;gap:7px;font-size:11px;color:var(--mut);text-align:right}
  .dot{width:7px;height:7px;border-radius:50%;background:var(--grn)}

  .panel{display:none}
  .pmeta{font-size:10.5px;color:#5a656a;line-height:1.6;padding:2px 4px 0}

  .card{background:var(--card);border:1px solid var(--edge);border-radius:16px;padding:15px 16px;margin-bottom:10px}
  .lbl{font-size:10.5px;letter-spacing:1.6px;color:var(--mut);font-weight:700;text-transform:uppercase}
  .pill{font-size:10px;font-weight:700;letter-spacing:.6px;padding:4px 8px;border-radius:20px;display:inline-flex;gap:4px}
  .pill.red{background:rgba(255,93,106,.13);color:var(--red)}
  .pill.grn{background:rgba(47,224,166,.12);color:var(--grn)}

  .regime .row1{display:flex;justify-content:space-between;align-items:center;margin-bottom:9px}
  .regime .big{font-size:25px;font-weight:700;letter-spacing:.2px}
  .regime .sub{font-size:12.5px;color:#9aa4a9;margin-top:1px}
  .meta{display:flex;gap:16px;margin-top:13px;padding-top:12px;border-top:1px solid var(--edge)}
  .meta div{font-size:11px;color:var(--mut)}
  .meta b{display:block;font-size:14px;color:var(--txt);margin-top:3px;font-family:var(--mono)}

  .emv .r{display:flex;justify-content:space-between;align-items:flex-end;margin:10px 0 4px}
  .emv .pct{font-size:29px;font-weight:700}
  .emv .dol{font-size:16px;color:#aeb8bd;font-family:var(--mono)}
  .track{position:relative;height:5px;border-radius:3px;margin:20px 0 7px;
    background:linear-gradient(90deg,var(--red-dim),#2a3236 46%,#2a3236 54%,var(--grn-dim))}
  .track .mk{position:absolute;top:50%;width:12px;height:12px;border-radius:50%;background:#e9eef0;
    border:2px solid #08090a;transform:translate(-50%,-50%);box-shadow:0 0 0 1px #2fe0a6}
  .ends{display:flex;justify-content:space-between;font-size:11px}
  .ends .lo{color:var(--red)}.ends .hi{color:var(--grn)}.ends .sp{color:var(--mut);text-align:center}
  .ends b{display:block;font-family:var(--mono);font-size:12.5px;margin-top:2px;color:var(--txt)}
  .ends .lo b{color:var(--red)}.ends .hi b{color:var(--grn)}
  .fine{font-size:11px;color:var(--mut);margin-top:12px;line-height:1.5}

  .two{display:flex;gap:12px;margin:12px 0 4px}
  .two>div{flex:1}
  .k{font-size:11px;color:var(--mut);margin-bottom:5px}
  .v{font-size:22px;font-weight:700;font-family:var(--mono)}
  .v.red{color:var(--red)}.v.grn{color:var(--grn)}
  .u{font-size:10.5px;color:var(--mut);margin-top:3px}
  .u.g{color:var(--grn-dim)}.u.r{color:var(--red-dim)}
  .brk{margin-top:15px;padding-top:13px;border-top:1px solid var(--edge)}
  .brk .h{display:flex;justify-content:space-between;align-items:center;margin-bottom:11px}
  .brk .h .t{font-size:10.5px;letter-spacing:1.4px;color:var(--mut);font-weight:700}
  .brk .h .tot{font-size:11px;color:var(--mut);font-family:var(--mono)}
  .bar{display:flex;align-items:center;gap:10px;margin:8px 0}
  .bar .nm{width:60px;font-size:11px;color:#9aa4a9;flex-shrink:0}
  .bar .tr{flex:1;height:7px;background:#0c1113;border-radius:4px;position:relative;overflow:hidden}
  .bar .fill{position:absolute;top:0;height:100%;border-radius:4px;opacity:.9}
  .bar .vl{width:74px;text-align:right;font-family:var(--mono);font-size:12px;flex-shrink:0}
  .bar .vl.red{color:var(--red)}.bar .vl.grn{color:var(--grn)}
  .lean{margin-top:14px;padding:11px 12px;background:var(--card2);border:1px solid #26454a;border-radius:11px;
    display:flex;justify-content:space-between;align-items:center;gap:10px}
  .lean .lt{font-size:11px;color:#9aa4a9;line-height:1.45}
  .lean .lt b{color:var(--txt)}
  .lean .rt{font-size:12px;color:var(--txt);font-family:var(--mono);white-space:nowrap}

  .pcbar{position:relative;height:8px;border-radius:5px;margin:12px 0 8px;overflow:hidden;display:flex}
  .pcbar .cf{background:var(--grn-dim)}.pcbar .pf{background:var(--red-dim)}
  .pcrow{display:flex;justify-content:space-between;font-size:11px}
  .pcrow .c{color:var(--grn)}.pcrow .p{color:var(--red)}
  .pcrow b{font-family:var(--mono);color:var(--txt);font-weight:600}

  .sk .grid{display:flex;gap:9px;margin:12px 0 2px}
  .sk .cell{flex:1;background:var(--card2);border:1px solid var(--edge);border-radius:11px;padding:10px 11px}
  .sk .cell .kk{font-size:9.5px;color:var(--mut);letter-spacing:.5px;margin-bottom:6px}
  .sk .cell .vv{font-size:19px;font-weight:700;font-family:var(--mono)}
  .sk .cell .vv.red{color:var(--red)}.sk .cell .vv.grn{color:var(--grn)}
  .sk .cell .vv.na{color:var(--mut);font-size:14px}
  .sk .cell .ss{font-size:10px;color:var(--mut);margin-top:4px}

  .foot{margin-top:6px;padding:0 4px}
  .foot p{font-size:10.5px;color:#5a656a;line-height:1.6;margin-bottom:6px}
  .foot .ok{color:var(--grn-dim)}
__TOGGLE_CSS__
</style>"""


def _render_html(payloads: dict) -> str:
    symbols = [s for s in payloads if payloads.get(s)]
    if not symbols:
        symbols = []
    # radio inputs (first checked), toggle labels, per-symbol panels, and the
    # :checked CSS that shows one panel + highlights its tab — all JS-free.
    radios, labels, panels, toggle_css = [], [], [], []
    for i, s in enumerate(symbols):
        rid = f"tg-{_html.escape(s)}"
        chk = " checked" if i == 0 else ""
        radios.append(f'<input class="tgr" type="radio" name="sym" id="{rid}"{chk}>')
        labels.append(f'<label for="{rid}">{_html.escape(s)}</label>')
        panels.append(f'<section class="panel panel-{_html.escape(s)}">{_panel(payloads[s])}</section>')
        toggle_css.append(
            f'  #{rid}:checked~.top .toggle label[for="{rid}"]'
            "{background:rgba(47,224,166,.12);border-color:#2b5;color:var(--grn)}\n"
            f'  #{rid}:checked~.panels .panel-{_html.escape(s)}{{display:block}}'
        )
    if not symbols:
        panels.append('<section class="panel" style="display:block"><div class="card">No snapshot available.</div></section>')

    style = _STYLE.replace("__TOGGLE_CSS__", "\n".join(toggle_css))
    return (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">\n'
        "<title>Dealer Positioning</title>\n"
        f"{style}\n</head>\n<body>\n<div class=\"app\">\n"
        f'{"".join(radios)}\n'
        '<div class="top"><div class="toggle">'
        f'{"".join(labels)}</div>'
        '<div class="status"><span class="dot"></span><span>snapshot</span></div></div>\n'
        f'<div class="panels">{"".join(panels)}</div>\n'
        '<div class="foot"><p><b class="ok">● Snapshot</b> from the Convex-fed DB '
        "(near-live at the scheduler cadence) — generated report, no live service, "
        "no added Convex calls.</p></div>\n"
        "</div>\n</body>\n</html>\n"
    )


def _collect(session, symbols: tuple[str, ...]) -> dict:
    """Build the per-symbol cockpit payloads from the Convex-fed DB (no vendor calls)."""
    from trading_intel.api.positioning import build_positioning

    payloads: dict = {}
    for sym in symbols:
        try:
            payloads[sym] = build_positioning(session, sym)
        except Exception as exc:  # noqa: BLE001 — one bad symbol shouldn't kill the report
            log.warning("cockpit.symbol_failed", symbol=sym, error=str(exc))
    return payloads


def build(
    *,
    symbols: tuple[str, ...] | None = None,
    out_path: str | None = None,
    settings: object = None,
    session: object = None,
) -> str:
    """Render the latest positioning snapshot into one self-contained HTML file.

    Symbols default to the configured index roots (``INDEX_ROOTS`` = SPX/SPY/QQQ)
    when not given. Reads the Convex-fed DB via ``api.positioning.build_positioning``
    (no vendor calls). Rendered entirely server-side (no <script>) so it opens on
    the phone. Opens its own session unless one is passed. Returns the absolute path.
    """
    from trading_intel.config import get_settings

    settings = settings or get_settings()
    roots = tuple(symbols) if symbols else tuple(getattr(settings, "index_roots", None) or _SYMBOLS)
    if session is not None:
        payloads = _collect(session, roots)
    else:
        from trading_intel.memory.db import make_session_factory

        with make_session_factory(settings)() as s:
            payloads = _collect(s, roots)

    html = _render_html(payloads)
    out = (Path(out_path) if out_path else _DEFAULT_OUT).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return str(out)


def run(
    *,
    symbols: tuple[str, ...] | None = None,
    push: bool = True,
    settings: object = None,
) -> str:
    """Build the cockpit and (optionally) push it to Telegram. Returns the path."""
    from trading_intel.config import get_settings

    settings = settings or get_settings()
    path = build(symbols=symbols, settings=settings)
    if push:
        from trading_intel.clients.telegram import TelegramClient

        sent = TelegramClient(settings).send_document(
            path, caption="Index dealer-positioning cockpit (SPX / SPY / QQQ)"
        )
        log.info("cockpit.pushed", path=path, telegram_sent=sent)
    return path


def main() -> None:
    """Manual/scheduled entrypoint: build the cockpit and push it to Telegram."""
    import argparse

    parser = argparse.ArgumentParser(description="Build the dealer-positioning cockpit report.")
    parser.add_argument("--no-push", action="store_true", help="build only; do not push to Telegram")
    parser.add_argument(
        "--symbols",
        default=",".join(_SYMBOLS),
        help="comma-separated index roots to bake in (default: config INDEX_ROOTS = SPX,SPY,QQQ)",
    )
    args = parser.parse_args()

    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    symbols = tuple(s.strip().upper() for s in args.symbols.split(",") if s.strip())
    path = run(symbols=symbols or _SYMBOLS, push=not args.no_push)
    print(f"cockpit written: {path}")


if __name__ == "__main__":
    main()
