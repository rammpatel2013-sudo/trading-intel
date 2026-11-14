"""Per-ticker dashboard page (Roadmap A1 / Phase 2).

Surfaces, for one watchlist symbol, everything the collectors now store:

1. Price + 20-day SMA + Bollinger bands, with the aggregate GEX time series
   overlaid on a secondary axis.
2. Net GEX by strike (bar) + a rolling average across strikes + a descriptive
   normal-distribution fit, marking the gamma-flip price and spot.
3. Net DEX by strike (bar) + rolling average.
4. RSI(14).

Plus the call/put-wall panel and the day-over-day change panels (these light up
once >= 2 daily ``greeks_chain`` snapshots have accrued).

The page is a thin shell: all data prep lives in ``dashboard/ticker_data.py``
(pure, unit-tested) and the wall/change markdown comes from the existing
``dashboard/walls.py`` and ``dashboard/changes.py``. Per the FlashAlpha rule
(CLAUDE.md rule 4) every panel here is a *regime descriptor* — no signals.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from trading_intel.config import get_settings
from trading_intel.dashboard.changes import (
    build_change_report,
    fixed_strike_change_matrix,
    load_fixed_strike_changes,
)
from trading_intel.dashboard.freshness import format_et, freshness_caption
from trading_intel.dashboard.live_gex_data import load_live_chain
from trading_intel.dashboard.ticker_data import (
    available_chain_dates,
    bollinger_bands,
    dex_by_strike,
    gex_by_strike,
    latest_snapshot,
    load_chain_at,
    load_latest_chain,
    load_quotes,
    load_snapshot_history,
    near_spot,
    normal_fit_by_strike,
    rolling_avg_by_strike,
    rsi,
    sma,
    snapshot_spot_flip,
)
from trading_intel.dashboard.walls import build_wall_report, wall_history_frame
from trading_intel.errors import TradingIntelError
from trading_intel.prices.fibonacci import FibLevels, fib_levels

_POS = "#2ecc71"
_NEG = "#e74c3c"
_ACCENT = "#e84393"
_GOLD = "#f6c343"

# Index symbols -> their yfinance quote ticker (for the live spot fetch).
_YF_MAP = {"SPX": "^GSPC", "NDX": "^NDX", "RUT": "^RUT", "VIX": "^VIX"}


def _live_spot(symbol: str) -> float | None:
    """Best-effort live spot via yfinance (index symbols mapped); None on failure.

    The stored GEX/DEX profile stays from the snapshot — this only moves the spot
    *marker* so it tracks the current tape.
    """
    try:
        import yfinance as yf

        px = getattr(yf.Ticker(_YF_MAP.get(symbol, symbol)).fast_info, "last_price", None)
        return float(px) if px else None
    except Exception:  # live quote is best-effort; callers fall back to snapshot spot
        return None


def _session_factory() -> sessionmaker[Session]:
    """Reuse the factory the Home composition root injected, else build one."""
    factory = st.session_state.get("session_factory")
    if factory is None:
        from trading_intel.memory.db import make_session_factory

        factory = make_session_factory(get_settings())
        st.session_state["session_factory"] = factory
    return factory


def _price_history(session: Session, symbol: str, *, lookback_days: int = 250) -> pd.DataFrame:
    """Daily price frame: stored ``quotes_daily`` first, else a yfinance fallback."""
    quotes = load_quotes(session, symbol, days=lookback_days)
    if not quotes.empty:
        return quotes
    try:
        import yfinance as yf

        raw = yf.Ticker(symbol).history(period="1y", auto_adjust=False)
    except (ImportError, OSError, ValueError, KeyError):
        # Best-effort fallback only: with no stored quotes the price panel is
        # simply skipped if yfinance is unavailable or the fetch fails.
        return quotes
    if raw is None or raw.empty:
        return quotes
    raw = raw.reset_index().rename(
        columns={
            "Date": "date",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )
    raw["date"] = pd.to_datetime(raw["date"]).dt.tz_localize(None)
    return raw[["date", "open", "high", "low", "close", "volume"]]


def _price_panel(prices: pd.DataFrame, snaps: pd.DataFrame, symbol: str) -> go.Figure | None:
    if prices.empty:
        return None
    close = prices["close"]
    bands = bollinger_bands(close, window=20)
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(
        go.Scatter(
            x=prices["date"], y=bands.upper, line={"color": "rgba(150,150,150,0.4)"},
            name="BB upper", hoverinfo="skip",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=prices["date"], y=bands.lower, line={"color": "rgba(150,150,150,0.4)"},
            fill="tonexty", fillcolor="rgba(150,150,150,0.12)", name="BB lower",
            hoverinfo="skip",
        )
    )
    fig.add_trace(
        go.Scatter(x=prices["date"], y=close, line={"color": _ACCENT}, name="Close")
    )
    fig.add_trace(
        go.Scatter(
            x=prices["date"], y=sma(close, 20), line={"color": _GOLD, "dash": "dot"},
            name="SMA20",
        )
    )
    if not snaps.empty and snaps["gex_total"].notna().any():
        fig.add_trace(
            go.Scatter(
                x=snaps["ts"], y=snaps["gex_total"], line={"color": "#5dade2"},
                mode="lines+markers", name="GEX (net gxoi)",
            ),
            secondary_y=True,
        )
    fig.update_layout(
        title=f"{symbol} — price, SMA20, Bollinger + GEX overlay",
        template="plotly_dark", height=420, margin={"l": 10, "r": 10, "t": 50, "b": 10},
        legend={"orientation": "h", "y": 1.04},
    )
    fig.update_yaxes(title_text="Price", secondary_y=False)
    fig.update_yaxes(title_text="GEX", secondary_y=True, showgrid=False)
    return fig


def _exposure_bar(
    by_strike: pd.DataFrame, value_col: str, *, title: str, ytitle: str,
    flip: float | None = None, spot: float | None = None, with_fit: bool = False,
) -> go.Figure | None:
    if by_strike.empty:
        return None
    colors = [_POS if v >= 0 else _NEG for v in by_strike[value_col]]
    fig = go.Figure()
    fig.add_trace(
        go.Bar(x=by_strike["strike"], y=by_strike[value_col], marker_color=colors, name=ytitle)
    )
    roll = rolling_avg_by_strike(by_strike, value_col, window=5)
    if not roll.empty:
        fig.add_trace(
            go.Scatter(
                x=by_strike["strike"], y=roll, mode="lines",
                line={"color": _GOLD, "width": 2}, name="rolling avg",
            )
        )
    if with_fit:
        fit = normal_fit_by_strike(by_strike, value_col)
        if fit is not None:
            fig.add_trace(
                go.Scatter(
                    x=fit.strike, y=fit.fit, mode="lines",
                    line={"color": "#bb8fce", "dash": "dash"}, name="normal fit",
                )
            )
    if spot is not None:
        fig.add_vline(
            x=spot, line_color=_ACCENT, line_dash="solid",
            annotation_text=f"spot {spot:g}", annotation_position="top",
        )
    if flip is not None:
        fig.add_vline(
            x=flip, line_color="#5dade2", line_dash="dot",
            annotation_text=f"flip {flip:g}", annotation_position="bottom",
        )
    fig.update_layout(
        title=title, template="plotly_dark", height=360,
        margin={"l": 10, "r": 10, "t": 50, "b": 10}, legend={"orientation": "h", "y": 1.06},
        bargap=0.1,
    )
    fig.update_xaxes(title_text="Strike")
    fig.update_yaxes(title_text=ytitle)
    return fig


def _rsi_panel(prices: pd.DataFrame, symbol: str) -> go.Figure | None:
    if prices.empty:
        return None
    values = rsi(prices["close"], window=14)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=prices["date"], y=values, line={"color": _ACCENT}, name="RSI(14)"))
    fig.add_hline(y=70, line_color="rgba(231,76,60,0.6)", line_dash="dash")
    fig.add_hline(y=30, line_color="rgba(46,204,113,0.6)", line_dash="dash")
    fig.update_layout(
        title=f"{symbol} — RSI(14)", template="plotly_dark", height=280,
        margin={"l": 10, "r": 10, "t": 50, "b": 10},
    )
    fig.update_yaxes(title_text="RSI", range=[0, 100])
    return fig


def _add_fib_levels(fig: go.Figure, fib: FibLevels | None) -> None:
    """Overlay Fibonacci retracement/extension levels as horizontal lines."""
    if fib is None:
        return
    key = {"38.2%", "50.0%", "61.8%"}
    for label, price in fib.levels.items():
        strong = label in key
        fig.add_hline(
            y=price,
            line_color="rgba(246,195,67,0.55)" if strong else "rgba(246,195,67,0.22)",
            line_dash="dot",
            annotation_text=f"fib {label}",
            annotation_position="right",
        )


def _fixed_strike_panel(
    changes: pd.DataFrame | None,
    symbol: str,
    *,
    call_wall: float | None = None,
    put_wall: float | None = None,
) -> go.Figure | None:
    matrix = fixed_strike_change_matrix(changes)
    if matrix.empty:
        return None
    # Diverging, zero-centred ΔIV heatmap across the full strike x expiry grid —
    # far more legible than a single front-expiry bar. Red = IV fell, blue = rose.
    x = [pd.Timestamp(c).date().isoformat() if not isinstance(c, str) else str(c)
         for c in matrix.columns]
    y = [float(s) for s in matrix.index]
    zmax = float(matrix.abs().max().max())
    zmax = zmax if zmax and zmax == zmax else 1.0  # guard NaN/0
    fig = go.Figure(
        go.Heatmap(
            z=matrix.to_numpy(), x=x, y=y, colorscale="RdBu", reversescale=True,
            zmid=0.0, zmin=-zmax, zmax=zmax, colorbar={"title": "ΔIV pts"},
        )
    )
    if call_wall is not None:
        fig.add_hline(y=call_wall, line_color=_POS, line_dash="dot",
                      annotation_text="call wall", annotation_position="right")
    if put_wall is not None:
        fig.add_hline(y=put_wall, line_color=_NEG, line_dash="dot",
                      annotation_text="put wall", annotation_position="right")
    fig.update_layout(
        title=f"{symbol} — fixed-strike ΔIV (strike x expiry)",
        template="plotly_dark", height=360,
        margin={"l": 10, "r": 10, "t": 50, "b": 10},
    )
    fig.update_xaxes(title_text="Expiry")
    fig.update_yaxes(title_text="Strike")
    return fig


def _wall_drift_panel(frame: pd.DataFrame, symbol: str) -> go.Figure | None:
    if frame is None or frame.empty or len(frame) < 2:
        return None
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=frame["date"], y=frame["call_wall"], mode="lines+markers",
                             name="call wall", line={"color": _POS}))
    fig.add_trace(go.Scatter(x=frame["date"], y=frame["put_wall"], mode="lines+markers",
                             name="put wall", line={"color": _NEG}))
    fig.update_layout(
        title=f"{symbol} — call/put wall drift", template="plotly_dark", height=320,
        margin={"l": 10, "r": 10, "t": 50, "b": 10}, legend={"orientation": "h", "y": 1.08},
    )
    fig.update_yaxes(title_text="Strike")
    return fig


def _atm_iv_panel(snaps: pd.DataFrame, symbol: str) -> go.Figure | None:
    """Day-over-day ATM implied vol from the aggregate snapshot history."""
    if snaps is None or snaps.empty or "atm_iv" not in snaps.columns:
        return None
    iv = pd.to_numeric(snaps["atm_iv"], errors="coerce")
    if not iv.notna().any():
        return None
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(x=snaps["ts"], y=iv * 100.0, mode="lines+markers",
                   line={"color": _GOLD}, name="ATM IV %")
    )
    fig.update_layout(
        title=f"{symbol} — ATM IV over time (day over day)", template="plotly_dark",
        height=320, margin={"l": 10, "r": 10, "t": 50, "b": 10},
    )
    fig.update_yaxes(title_text="ATM IV %")
    return fig


def main() -> None:
    st.set_page_config(page_title="Ticker", page_icon="📊", layout="wide")
    settings = get_settings()
    symbols = settings.watchlist_symbols

    st.title("📊 Per-ticker view")
    st.caption("Regime descriptors only — GEX/DEX/walls are not trade signals (FlashAlpha rule).")

    symbol = st.sidebar.selectbox("Symbol", symbols, index=0 if symbols else None)
    if not symbol:
        st.warning("No symbols configured in the watchlist.")
        return

    pct = st.sidebar.slider("Strike range (± % of spot)", 1.0, 25.0, 8.0, 0.5)
    full_chain = st.sidebar.checkbox("Show full chain (ignore range)", value=False)
    pct_range = None if full_chain else pct / 100.0

    try:
        factory = _session_factory()
        with factory() as session:
            dates = available_chain_dates(session, symbol)
            chosen_ts = (
                st.sidebar.selectbox(
                    "Stored snapshot", dates, index=0,
                    format_func=lambda d: pd.Timestamp(d).strftime("%Y-%m-%d %H:%M"),
                    help="A historical stored chain snapshot - not a fresh live pull.",
                )
                if dates
                else None
            )
            if chosen_ts is not None:
                ts, chain = chosen_ts, load_chain_at(session, symbol, chosen_ts)
            else:
                ts, chain = load_latest_chain(session, symbol)
            snaps = load_snapshot_history(session, symbol)
            snap = latest_snapshot(session, symbol)
            prices = _price_history(session, symbol)
            change_md = build_change_report(session, symbol)
            wall_md = build_wall_report(session, symbol)
            wall_hist = wall_history_frame(session, symbol)
            fixed_changes = load_fixed_strike_changes(session, symbol)
            live_ts, live_chain = load_live_chain(session, symbol)
    except (SQLAlchemyError, TradingIntelError) as exc:
        st.error(f"Could not load data for {symbol}: {exc}")
        return

    # Spot/flip belong to the SELECTED snapshot; on the latest view prefer a live
    # quote so the spot marker tracks the tape (the GEX/DEX profile itself stays
    # from the stored snapshot).
    snap_spot, flip = snapshot_spot_flip(snaps, chosen_ts)
    is_latest = chosen_ts is None or (bool(dates) and chosen_ts == dates[0])
    live_spot = _live_spot(symbol) if is_latest else None
    spot = live_spot if live_spot is not None else snap_spot
    cw = wall_hist["call_wall"].iloc[-1] if not wall_hist.empty else None
    pw = wall_hist["put_wall"].iloc[-1] if not wall_hist.empty else None

    cols = st.columns(4)
    cols[0].metric(
        "Spot", f"{spot:g}" if spot is not None else "n/a",
        help="Live quote" if live_spot is not None else "From the stored snapshot",
    )
    cols[1].metric("GEX flip", f"{flip:g}" if flip is not None else "n/a")
    cols[2].metric(
        "GEX (net gxoi)",
        f"{snap.gex_total:,.0f}" if snap is not None and snap.gex_total is not None else "n/a",
    )
    cols[3].metric(
        "ATM IV",
        f"{snap.atm_iv * 100:.1f}%" if snap is not None and snap.atm_iv is not None else "n/a",
    )
    st.caption(freshness_caption(ts, label="Chain snapshot"))

    # Price + RSI directly below it (per request).
    fib = fib_levels(prices)
    price_fig = _price_panel(prices, snaps, symbol)
    if price_fig is not None:
        _add_fib_levels(price_fig, fib)
        st.plotly_chart(price_fig, use_container_width=True)
    else:
        st.info("No price history available (quotes_daily empty and yfinance unavailable).")

    rsi_fig = _rsi_panel(prices, symbol)
    if rsi_fig is not None:
        st.plotly_chart(rsi_fig, use_container_width=True)

    # Net GEX / DEX by strike, trimmed to the selected strike range; call/put
    # walls marked on the GEX panel. Prefer the fresh LIVE per-strike GEX on the
    # latest view, falling back to the stored snapshot chain.
    use_live = is_latest and live_ts is not None
    gex_chain = live_chain if use_live else chain
    if use_live:
        st.caption(
            f"GEX / DEX by strike: **LIVE** @ {format_et(live_ts)} "
            "(near-the-money delta band)."
        )
    gex = near_spot(gex_by_strike(gex_chain), spot, pct_range)
    dex = near_spot(dex_by_strike(gex_chain), spot, pct_range)
    left, right = st.columns(2)
    gex_fig = _exposure_bar(
        gex, "gex", title=f"{symbol} — net GEX by strike", ytitle="net gxoi",
        flip=flip, spot=spot, with_fit=True,
    )
    if gex_fig is not None:
        for wall, color, label in ((cw, _POS, "call wall"), (pw, _NEG, "put wall")):
            if wall is not None:
                gex_fig.add_vline(x=wall, line_color=color, line_dash="dash",
                                  annotation_text=label, annotation_position="top")
        left.plotly_chart(gex_fig, use_container_width=True)
    else:
        left.info("No per-strike chain stored yet for GEX.")
    dex_fig = _exposure_bar(
        dex, "dex", title=f"{symbol} — net DEX by strike", ytitle="net dxoi", spot=spot,
    )
    if dex_fig is not None:
        right.plotly_chart(dex_fig, use_container_width=True)
    else:
        right.info("No per-strike chain stored yet for DEX.")

    # Day-over-day vol change: fixed-strike ΔIV heatmap (trimmed to range) +
    # ATM IV over time.
    fs_fig = _fixed_strike_panel(
        near_spot(fixed_changes, spot, pct_range), symbol, call_wall=cw, put_wall=pw
    )
    atm_fig = _atm_iv_panel(snaps, symbol)
    fs_col, atm_col = st.columns(2)
    if fs_fig is not None:
        fs_col.plotly_chart(fs_fig, use_container_width=True)
    else:
        fs_col.info("Fixed-strike vol change needs >= 2 daily chain snapshots.")
    if atm_fig is not None:
        atm_col.plotly_chart(atm_fig, use_container_width=True)
    else:
        atm_col.info("ATM IV history needs stored greeks snapshots.")

    # Walls: the drift visualization (the table is secondary, in an expander).
    drift_fig = _wall_drift_panel(wall_hist, symbol)
    if drift_fig is not None:
        st.plotly_chart(drift_fig, use_container_width=True)
    else:
        st.info("Wall drift needs >= 2 days of snapshots.")

    with st.expander("Wall + day-over-day change tables"):
        wall_col, change_col = st.columns(2)
        wall_col.markdown(wall_md)
        change_col.markdown(change_md)


main()
