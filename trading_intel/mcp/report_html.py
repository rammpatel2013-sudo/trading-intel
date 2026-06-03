"""Standalone HTML chart report for a single ticker (plotly).

Positioning-first layout (no generic TA noise): a price candlestick with the
dealer gamma levels overlaid (flip + call/put walls), a volume strip, the net-GEX
history, and the 25-delta risk-reversal (skew) line. Used by the
``render_report_html`` MCP tool so Claude Desktop hands back a visual instead of
a JSON blob.

Pure rendering: takes already-loaded frames, writes a file, returns its path.
No DB or network I/O here, and nothing trade-signal-like - these are regime
descriptors (FlashAlpha rule 4). ``plotly`` is a project dependency.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_OUT = _REPO_ROOT / "reports"


def render_html_report(
    symbol: str,
    ohlc: pd.DataFrame,
    gamma_history: pd.DataFrame,
    *,
    walls: dict | None = None,
    skew: pd.DataFrame | None = None,
    out_dir: Path | None = None,
) -> str:
    """Write a stacked HTML chart for ``symbol`` and return the absolute path.

    ``ohlc``: date, open, high, low, close, volume (oldest-first).
    ``gamma_history``: ts, gex_total, gex_flip (oldest-first); may be empty.
    ``walls``: optional {"call_wall", "put_wall"} drawn on the price panel.
    ``skew``: optional ts + rr_25d (oldest-first) for the risk-reversal panel.
    Raises ``ValueError`` if there is nothing to plot.
    """
    from plotly.subplots import make_subplots  # local import keeps startup light

    has_price = ohlc is not None and not ohlc.empty
    has_gamma = gamma_history is not None and not gamma_history.empty
    has_skew = skew is not None and not skew.empty and "rr_25d" in skew.columns
    if not (has_price or has_gamma or has_skew):
        raise ValueError(f"no price/gamma/skew data to plot for {symbol}")

    sections: list[str] = []
    if has_price:
        sections += ["price", "volume"]
    if has_gamma:
        sections += ["gex"]
    if has_skew:
        sections += ["skew"]

    titles = {
        "price": f"{symbol} - price + dealer gamma levels",
        "volume": "Volume",
        "gex": "Net GEX history",
        "skew": "25d risk reversal (put IV - call IV)",
    }
    heights = {"price": 0.5, "volume": 0.13, "gex": 0.19, "skew": 0.18}
    raw = [heights[s] for s in sections]
    row_heights = [h / sum(raw) for h in raw]

    fig = make_subplots(
        rows=len(sections), cols=1, shared_xaxes=False,
        vertical_spacing=0.06, row_heights=row_heights,
        subplot_titles=[titles[s] for s in sections],
    )
    row_of = {name: i + 1 for i, name in enumerate(sections)}

    flip = _latest(gamma_history, "gex_flip") if has_gamma else None
    if has_price:
        _add_price(fig, ohlc, walls or {}, flip, row=row_of["price"])
        _add_volume(fig, ohlc, row=row_of["volume"])
    if has_gamma:
        _add_gamma(fig, gamma_history, row=row_of["gex"])
    if has_skew:
        _add_skew(fig, skew, row=row_of["skew"])

    fig.update_layout(
        template="plotly_dark", height=300 * len(sections), width=1100,
        showlegend=True, margin={"l": 60, "r": 30, "t": 60, "b": 40},
        title=f"{symbol} - trading-intel report ({datetime.now():%Y-%m-%d %H:%M})",
    )

    out = Path(out_dir) if out_dir else _DEFAULT_OUT
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{symbol}_{datetime.now():%Y%m%d_%H%M%S}.html"
    fig.write_html(str(path), include_plotlyjs="cdn", full_html=True)
    return str(path)


def _latest(df: pd.DataFrame, col: str) -> float | None:
    if df is None or df.empty or col not in df.columns:
        return None
    s = pd.to_numeric(df[col], errors="coerce").dropna()
    return float(s.iloc[-1]) if not s.empty else None


def _add_price(fig, ohlc, walls, flip, *, row):  # noqa: ANN001
    import plotly.graph_objects as go

    x = ohlc["date"]
    fig.add_trace(
        go.Candlestick(
            x=x, open=ohlc["open"], high=ohlc["high"],
            low=ohlc["low"], close=ohlc["close"], name="OHLC",
        ),
        row=row, col=1,
    )
    levels = (
        ("Gamma flip", flip, "#7f8c8d"),
        ("Call wall", walls.get("call_wall"), "#3fb950"),
        ("Put wall", walls.get("put_wall"), "#f85149"),
    )
    for label, value, color in levels:
        if value is None:
            continue
        fig.add_hline(
            y=float(value), line={"color": color, "width": 1.0, "dash": "dash"},
            annotation_text=f"{label} {float(value):g}",
            annotation_position="right", row=row, col=1,
        )
    fig.update_xaxes(rangeslider_visible=False, row=row, col=1)


def _add_volume(fig, ohlc, *, row):  # noqa: ANN001
    import plotly.graph_objects as go

    up = ohlc["close"].astype(float) >= ohlc["open"].astype(float)
    colors = ["rgba(63,185,80,.6)" if u else "rgba(248,81,73,.6)" for u in up]
    fig.add_trace(
        go.Bar(x=ohlc["date"], y=ohlc["volume"], name="Volume", marker={"color": colors}),
        row=row, col=1,
    )


def _add_gamma(fig, gamma, *, row):  # noqa: ANN001
    import plotly.graph_objects as go

    x = pd.to_datetime(gamma["ts"])
    fig.add_trace(go.Scatter(x=x, y=gamma["gex_total"], name="Net GEX",
                             line={"color": "#bc8cff"}), row=row, col=1)
    fig.add_hline(y=0, line={"color": "#bbbbbb", "width": 0.6}, row=row, col=1)


def _add_skew(fig, skew, *, row):  # noqa: ANN001
    import plotly.graph_objects as go

    x = pd.to_datetime(skew["ts"])
    fig.add_trace(go.Scatter(x=x, y=skew["rr_25d"], name="25d RR",
                             line={"color": "#e3b341"}), row=row, col=1)
    fig.add_hline(y=0, line={"color": "#bbbbbb", "width": 0.6}, row=row, col=1)
