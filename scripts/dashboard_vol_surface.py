"""SPX/SPY volatility-surface dashboard (LIVE half) — Plotly HTML.

Matches the LIVE columns of the ^SPX Volatility Surface reference: a delta
vol-surface table (3 nearest monthly-ish expiries), a 3D delta surface, the
front-expiry skew curve, and the term structure (ATM IV + forward vol). The
"vol changes" / "fixed-strike vol changes" panels are diffs vs a prior date and
arrive once the greeks_chain collector has >= 2 daily snapshots.

Run (Windows; Convex creds in .env, runs on the laptop):
    .venv\\Scripts\\python scripts\\dashboard_vol_surface.py
    .venv\\Scripts\\python scripts\\dashboard_vol_surface.py --symbol SPY
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from sqlalchemy.exc import SQLAlchemyError

from trading_intel.clients.convex import ConvexClient
from trading_intel.config import get_settings
from trading_intel.dashboard.changes import build_change_report
from trading_intel.dashboard.walls import build_wall_report
from trading_intel.errors import TradingIntelError
from trading_intel.greeks.surface import DeltaSurface, build_delta_surface, forward_vol
from trading_intel.memory.db import make_session_factory
from trading_intel.strategies.options_flow import (
    aggregate_flow,
    detect_structures,
    flowsum_by_expiry,
)
from trading_intel.synthesis.surface_report import build_surface_report, surface_metrics


def _table_rows(surface: DeltaSurface) -> tuple[list[str], list[list[int]]]:
    """Row labels (put 5..50 then call 47.5..5) + per-expiry column index maps."""
    d = surface.deltas
    put_labels = [f"{x:g}Δ P" for x in d]
    call_idx = list(range(len(d) - 2, -1, -1))  # exclude the 50 dup, descend
    call_labels = [f"{d[i]:g}Δ C" for i in call_idx]
    return put_labels + call_labels, [list(range(len(d))), call_idx]


def _expiry_column(surface: DeltaSurface, j: int, idx_maps: list[list[int]]) -> list[float]:
    put_idx, call_idx = idx_maps
    return [surface.iv_put[j, k] for k in put_idx] + [surface.iv_call[j, k] for k in call_idx]


def build_dashboard_figure(surface: DeltaSurface, *, symbol: str, spot: float) -> go.Figure:
    labels, idx_maps = _table_rows(surface)
    exp_names = [e.isoformat() for e in surface.expiries]
    columns = [_expiry_column(surface, j, idx_maps) for j in range(surface.n_expiries)]
    z = np.array([[v * 100 for v in col] for col in columns])  # (T, rows) in vol pts

    fig = make_subplots(
        rows=2,
        cols=2,
        specs=[
            [{"type": "table"}, {"type": "surface"}],
            [{"type": "xy"}, {"type": "xy"}],
        ],
        subplot_titles=("Vol surface (IV %)", "3D surface", "Front-expiry skew", "Term structure"),
        column_widths=[0.42, 0.58],
        row_heights=[0.55, 0.45],
    )

    # (1,1) table
    fig.add_trace(
        go.Table(
            header={
                "values": ["Delta", *exp_names],
                "fill_color": "#1f2a44",
                "font": {"color": "white"},
            },
            cells={
                "values": [
                    labels,
                    *[
                        [f"{v * 100:.2f}%" if np.isfinite(v) else "" for v in col]
                        for col in columns
                    ],
                ],
                "align": "center",
            },
        ),
        row=1,
        col=1,
    )

    # (1,2) 3D surface over (row-index, expiry)
    fig.add_trace(
        go.Surface(
            x=np.arange(len(labels)),
            y=surface.dte,
            z=z,
            colorscale="Viridis",
            showscale=False,
        ),
        row=1,
        col=2,
    )

    # (2,1) front-expiry skew (IV vs delta-row)
    fig.add_trace(
        go.Scatter(
            x=labels, y=z[0], mode="lines+markers", name=exp_names[0], line={"color": "#e84393"}
        ),
        row=2,
        col=1,
    )

    # (2,2) term structure: ATM IV (live) + forward vol
    atm = surface.atm_iv * 100
    fwd = forward_vol(surface.dte, surface.atm_iv) * 100
    fig.add_trace(
        go.Scatter(
            x=exp_names, y=atm, mode="lines+markers", name="ATM IV", line={"color": "#e84393"}
        ),
        row=2,
        col=2,
    )
    fig.add_trace(
        go.Scatter(
            x=exp_names,
            y=fwd,
            mode="lines+markers",
            name="Forward vol",
            line={"color": "#f6c343", "dash": "dot"},
        ),
        row=2,
        col=2,
    )

    fig.update_layout(
        title=f"{symbol} Volatility Surface (LIVE) - {date.today():%Y-%m-%d}  spot {spot:.2f}",
        template="plotly_dark",
        height=900,
        margin={"l": 10, "r": 10, "t": 60, "b": 10},
    )
    fig.update_yaxes(title_text="IV %", row=2, col=1)
    fig.update_yaxes(title_text="IV %", row=2, col=2)
    return fig


def _md_to_html(md: str) -> str:
    """Minimal markdown -> HTML (## headers, - bullets, paragraphs) for the report panel."""
    body: list[str] = []
    in_list = False
    for raw in md.splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            if in_list:
                body.append("</ul>")
                in_list = False
            body.append(f"<h3>{line[3:]}</h3>")
        elif line.startswith("- "):
            if not in_list:
                body.append("<ul>")
                in_list = True
            body.append(f"<li>{line[2:]}</li>")
        elif line:
            if in_list:
                body.append("</ul>")
                in_list = False
            body.append(f"<p>{line}</p>")
    if in_list:
        body.append("</ul>")
    inner = "\n".join(body)
    return (
        '<section style="background:#0e1117;color:#e6e6e6;font-family:system-ui,Arial;'
        'max-width:1100px;margin:16px auto;padding:8px 24px;border-top:1px solid #2a3550">'
        f"{inner}</section>"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="SPX/SPY vol-surface dashboard (live half).")
    parser.add_argument("--symbol", default="SPX")
    parser.add_argument("--out", default=None)
    parser.add_argument("--expiries", type=int, default=3)
    parser.add_argument(
        "--llm", action="store_true", help="Add an Ollama narrative grounded in the playbooks"
    )
    args = parser.parse_args()

    symbol = args.symbol.upper()
    client = ConvexClient(get_settings())
    chain = client.chain_long(symbol)
    spot = client._spot(symbol)  # underlying price (same access gex_rolling uses)

    surface = build_delta_surface(chain, n_expiries=args.expiries)
    fig = build_dashboard_figure(surface, symbol=symbol, spot=spot)

    metrics = surface_metrics(surface)

    # Flow + per-trade packages (best-effort: the surface still renders if the
    # flow endpoints are empty pre-open or error out). No live creds in CI.
    flow = flowsum = structures = None
    try:
        flow = aggregate_flow(client.flow_chain(symbol))
    except TradingIntelError as exc:
        print(f"flow tilt skipped: {exc}")
    try:
        flowsum = flowsum_by_expiry(client.flow_summary(symbol))
    except TradingIntelError as exc:
        print(f"flowsum skipped: {exc}")
    try:
        structures = detect_structures(client.time_and_sales(symbol, limit=500))
    except TradingIntelError as exc:
        print(f"packages skipped: {exc}")

    report_md = build_surface_report(
        metrics, flow=flow, flowsum=flowsum, structures=structures
    )
    if args.llm:
        from trading_intel.synthesis.llm import OllamaProvider
        from trading_intel.synthesis.surface_report import interpret_surface_llm, load_kb_context

        narrative = interpret_surface_llm(
            metrics, OllamaProvider(get_settings()), kb_text=load_kb_context()
        )
        report_md = f"{report_md}\n\n## LLM read-through (grounded in playbooks)\n{narrative}"

    # Day-over-day change panels (need >= 2 daily greeks_chain snapshots).
    try:
        session_factory = make_session_factory(get_settings())
        with session_factory() as session:
            report_md = f"{report_md}\n\n{build_change_report(session, symbol)}"
            report_md = f"{report_md}\n\n{build_wall_report(session, symbol)}"
    except (SQLAlchemyError, TradingIntelError) as exc:
        print(f"change panels skipped: {exc}")

    out = Path(args.out) if args.out else Path("reports") / f"{symbol.lower()}_vol_dashboard.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    html = fig.to_html(include_plotlyjs="cdn", full_html=True)
    html = html.replace("</body>", _md_to_html(report_md) + "</body>")
    out.write_text(html, encoding="utf-8")
    print(
        f"Wrote {out}  ({surface.n_expiries} expiries: {[e.isoformat() for e in surface.expiries]})"
    )


if __name__ == "__main__":
    main()
