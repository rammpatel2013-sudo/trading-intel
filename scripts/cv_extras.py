#!/usr/bin/env python3
"""On-demand ConvexValue "extras" digest -> reports/cv_extras_<date>.html.

Pulls the ConvexValue endpoints beyond convexlib's core (earnings + economic
calendars, the native vflowratio flow scanner, per-name IV term structure, and
index net-flow via flowchart) through ``clients/convex_app.ConvexAppClient`` and
renders one dark-theme HTML digest, then the .bat opens it.

Descriptive data only -- not signals (FlashAlpha rule 4).

Run (Windows, venv):
    run_cv_extras.bat
    .venv\\Scripts\\python scripts\\cv_extras.py
"""

from __future__ import annotations

import html
from datetime import date, timedelta
from pathlib import Path

from trading_intel.clients.convex_app import ConvexAppClient
from trading_intel.config import get_settings
from trading_intel.errors import DataSourceError

_OUT = Path(__file__).resolve().parents[1] / "reports"
GRN, RED, NEU = "#4ade80", "#f87171", "#cbd5e1"
_STYLE = """
body{background:#0e1117;color:#e6e6e6;font-family:system-ui,Arial,sans-serif;margin:0;padding:24px;}
h1{font-size:20px;margin:0 0 2px;} h2{font-size:15px;margin:22px 0 6px;color:#cbd5e1;}
.sub{color:#8b97a7;font-size:12px;margin:0 0 10px;}
table{border-collapse:collapse;width:100%;font-size:12.5px;margin-bottom:6px;}
th,td{padding:5px 9px;text-align:right;border-bottom:1px solid #2a3550;}
th{background:#1f2a44;color:#fff;} td:first-child,th:first-child{text-align:left;font-weight:600;}
.note{color:#8b97a7;font-size:11.5px;margin-top:14px;line-height:1.5;}
.err{color:#f87171;}
"""


def _f(x: object, dp: int = 2) -> str:
    try:
        return f"{float(x):,.{dp}f}"
    except (TypeError, ValueError):
        return "—"


def _rows(data: dict) -> list:
    """Calendar/scan payloads are {data:[header, rows]} or {data:[rows]}."""
    d = data.get("data") or []
    if len(d) >= 2 and isinstance(d[1], list):
        return d[1]
    return d[0] if d and isinstance(d[0], list) else []


def _table(head: list[str], body_rows: list[str]) -> str:
    if not body_rows:
        return "<p class='sub'>none</p>"
    h = "".join(f"<th>{html.escape(c)}</th>" for c in head)
    return f"<table><tr>{h}</tr>{''.join(body_rows)}</table>"


def earnings_section(client: ConvexAppClient, watch: set[str]) -> str:
    rows = _rows(client.earnings_calendar(days=30))
    today = date.today().isoformat()
    hit = [r for r in rows if len(r) > 1 and str(r[1]).upper() in watch and str(r[0]) >= today]
    hit.sort(key=lambda r: str(r[0]))
    out = [
        f"<tr><td>{html.escape(str(r[1]))}</td><td>{html.escape(str(r[0]))}</td>"
        f"<td>{html.escape(str(r[4]) if len(r) > 4 and r[4] else '—')}</td>"
        f"<td>{_f(r[3] if len(r) > 3 else None)}</td></tr>"
        for r in hit
    ]
    return "<h2>Watchlist earnings — next 30d</h2>" + _table(
        ["Sym", "Date", "Time", "EPS est"], out
    )


def econ_section(client: ConvexAppClient) -> str:
    rows = _rows(client.economic_calendar(days=7))
    keep = [r for r in rows if len(r) > 8 and str(r[8]) in ("High", "Medium")]
    out = [
        f"<tr><td>{html.escape(str(r[2])[:48])}</td><td>{html.escape(str(r[0])[:16])}</td>"
        f"<td>{html.escape(str(r[1]))}</td><td>{html.escape(str(r[8]))}</td>"
        f"<td>{_f(r[5] if len(r) > 5 else None)}</td><td>{_f(r[4] if len(r) > 4 else None)}</td></tr>"
        for r in keep[:40]
    ]
    return "<h2>Economic calendar — next 7d (High/Medium)</h2>" + _table(
        ["Event", "When", "Ctry", "Impact", "Est", "Prev"], out
    )


def flow_section(client: ConvexAppClient) -> str:
    rows = _rows(client.flow_scan(min_value=1_000_000, limit=25))
    out = []
    for r in rows:
        if len(r) < 5:
            continue
        chg = r[3]
        col = GRN if (isinstance(chg, (int, float)) and chg > 0) else RED
        out.append(
            f"<tr><td>{html.escape(str(r[0]))}</td><td>${_f(r[1], 0)}</td>"
            f"<td>{_f(r[2])}</td><td style='color:{col}'>{_f(chg)}</td>"
            f"<td><b>{_f(r[4])}</b></td></tr>"
        )
    return "<h2>Flow scan — top vflowratio (value &gt; $1M)</h2>" + _table(
        ["Sym", "Premium", "Price", "Chg", "vflowratio"], out
    )


def term_section(client: ConvexAppClient, symbols: list[str]) -> str:
    series = (client.trm_chain(symbols).get("data") or [{}])[0].get("series") or []
    by_sym: dict[str, list[tuple[int, float]]] = {}
    today_id = (date.today() - date(1970, 1, 1)).days
    for el in series:
        if (
            not isinstance(el, list)
            or len(el) < 2
            or not isinstance(el[0], str)
            or "-" not in el[0]
        ):
            continue
        sym = el[0].split("-")[0].lstrip("#").upper()
        try:
            expday, iv = int(el[0].split("-")[1]), float(el[1])
        except (ValueError, IndexError):
            continue
        if iv > 0:
            by_sym.setdefault(sym, []).append((expday, iv))
    out = []
    for sym in symbols:
        pts = sorted(by_sym.get(sym.upper(), []))
        if not pts:
            continue
        front_dte, front_iv = pts[0][0] - today_id, pts[0][1]
        back_dte, back_iv = pts[-1][0] - today_id, pts[-1][1]
        slope = (front_iv - back_iv) * 100
        col = RED if slope > 0 else GRN  # front>back = backwardation (stress)
        out.append(
            f"<tr><td>{html.escape(sym.upper())}</td>"
            f"<td>{_f(front_iv * 100, 1)}% ({front_dte}d)</td>"
            f"<td>{_f(back_iv * 100, 1)}% ({back_dte}d)</td>"
            f"<td style='color:{col}'>{_f(slope, 1)}</td></tr>"
        )
    return "<h2>IV term structure — watchlist</h2>" + _table(
        ["Sym", "Front IV", "Back IV", "Slope (pts)"], out
    )


def market_flow_section(client: ConvexAppClient) -> str:
    out = []
    for sym in ("SPY", "QQQ"):
        rows = _rows(client.flowchart(sym))
        last = rows[-1] if rows else None
        if not last or len(last) < 4:
            continue
        fn, vfn = last[2], last[3]
        col = GRN if (isinstance(fn, (int, float)) and fn > 0) else RED
        out.append(
            f"<tr><td>{sym}</td><td style='color:{col}'>{_f(fn, 0)}</td><td>{_f(vfn, 0)}</td></tr>"
        )
    return "<h2>Index net-flow (latest)</h2>" + _table(["Sym", "flownet", "vflownet"], out)


def _safe(fn) -> str:  # noqa: ANN001
    try:
        return fn()
    except DataSourceError as exc:
        return f"<p class='err'>section failed: {html.escape(str(exc)[:120])}</p>"


def main() -> None:
    settings = get_settings()
    watch = settings.watchlist_symbols
    client = ConvexAppClient(settings)
    client.login()
    try:
        sections = [
            _safe(lambda: earnings_section(client, set(watch))),
            _safe(lambda: econ_section(client)),
            _safe(lambda: flow_section(client)),
            _safe(lambda: term_section(client, watch)),
            _safe(lambda: market_flow_section(client)),
        ]
    finally:
        client.close()

    body = "".join(sections)
    generated = date.today().isoformat()
    doc = (
        "<!doctype html><html><head><meta charset='utf-8'><title>ConvexValue extras</title>"
        f"<style>{_STYLE}</style></head><body><h1>ConvexValue extras &mdash; digest</h1>"
        f"<p class='sub'>Live ConvexValue pull &middot; generated {generated}</p>{body}"
        "<p class='note'>Earnings/econ calendars, native vflowratio flow scan, per-name IV term "
        "structure, and index net-flow &mdash; endpoints beyond convexlib's core, via the same pro "
        "login (dealer <code>matrix</code> also available on the client). Descriptive only, not "
        "signals (FlashAlpha rule 4).</p></body></html>"
    )
    _OUT.mkdir(exist_ok=True)
    out = _OUT / f"cv_extras_{generated}.html"
    out.write_text(doc, encoding="utf-8")
    print(f"cv extras digest -> {out}")


if __name__ == "__main__":
    main()
