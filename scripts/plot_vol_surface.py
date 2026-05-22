"""Render an interactive 3D implied-vol surface (Plotly HTML) for a symbol.

Pulls a wide live chain via ConvexClient, builds a moneyness x tenor IV grid
(``greeks/surface.py``), and writes a self-contained interactive HTML you can
open in a browser (rotate / hover IV). Snapshot only — the time-series /
sticky-strike work comes next, off the persisted ``greeks_chain`` table.

Run (Windows; Convex creds in .env, runs on the laptop):
    .venv\\Scripts\\python scripts\\plot_vol_surface.py
    .venv\\Scripts\\python scripts\\plot_vol_surface.py --symbol SPX --max-dte 365
    .venv\\Scripts\\python scripts\\plot_vol_surface.py --symbol SPY
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import plotly.graph_objects as go

from trading_intel.clients.convex import ConvexClient
from trading_intel.config import get_settings
from trading_intel.greeks.surface import build_surface_grid


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot a 3D implied-vol surface (Plotly HTML).")
    parser.add_argument("--symbol", default="SPX")
    parser.add_argument(
        "--out", default=None, help="Output HTML path (default reports/<sym>_iv_surface.html)"
    )
    parser.add_argument("--max-dte", type=int, default=365)
    args = parser.parse_args()

    symbol = args.symbol.upper()
    client = ConvexClient(get_settings())

    chain = client.chain_long(symbol)
    spot = client._spot(symbol)  # underlying price (same access gex_rolling uses)
    surface = build_surface_grid(chain, spot, max_dte=args.max_dte)

    z = surface.iv * 100.0  # vol points (%)
    fig = go.Figure(
        data=[
            go.Surface(
                x=surface.moneyness,
                y=surface.dte,
                z=z,
                colorscale="Viridis",
                colorbar={"title": "IV %"},
                hovertemplate=("moneyness %{x:.2f}<br>tenor %{y} d<br>IV %{z:.1f}%<extra></extra>"),
            )
        ]
    )
    fig.update_layout(
        title=f"{symbol} Implied Volatility Surface — {date.today():%Y-%m-%d} (spot {spot:.2f})",
        scene={
            "xaxis_title": "Moneyness (K / S)",
            "yaxis_title": "Tenor (days to expiry)",
            "zaxis_title": "Implied vol (%)",
        },
        autosize=True,
        margin={"l": 0, "r": 0, "t": 40, "b": 0},
    )

    out = Path(args.out) if args.out else Path("reports") / f"{symbol.lower()}_iv_surface.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(str(out), include_plotlyjs=True, full_html=True)
    print(
        f"Wrote {out}  ({surface.n_expiries} expiries x "
        f"{len(surface.moneyness)} moneyness nodes, spot={spot:.2f})"
    )


if __name__ == "__main__":
    main()
