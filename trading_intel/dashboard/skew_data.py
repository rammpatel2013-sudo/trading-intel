"""Data-prep for the Skew dashboard page (page 17).

Three views:

1. **Per-name view** — latest ``skew_snapshots`` row per symbol/horizon, plus
   the trailing RR band and the price overlay (joined from ``quotes_daily``).
   Reproduces the MU reference image.
2. **Index time series** — ``index_skew_daily`` long-form, plus per-name RR
   roll-up across SPY/QQQ/IWM as the structural-index overlay. Reproduces the
   SPX-style time-series chart.
3. **VIX-options view** — today's ``vix_options_chain`` rows aggregated by
   strike (call-wing IV, OI distribution), plus the trailing
   ``vix_tail_hedging_score`` for the regime context.

Side-effect-free; unit-testable on in-memory SQLite (creating just the three
new tables + ``quotes_daily``).
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.memory.models import (
    IndexSkewDaily,
    QuoteDaily,
    SkewSnapshot,
    VixOptionsChain,
)

# ── Per-name view ──────────────────────────────────────────────────────


def per_name_latest(
    session: Session, *, horizon_dte: int = 30
) -> pd.DataFrame:
    """Latest skew row per symbol at one horizon — for the dashboard sheet.

    Sorted by ``rr_25d_pctile_252d`` ascending so the deepest call bias names
    (the MU pattern) sit at the top. Cold rows fall to the bottom.
    """
    sub = (
        select(SkewSnapshot.symbol, SkewSnapshot.ts)
        .where(SkewSnapshot.horizon_dte == horizon_dte)
        .order_by(SkewSnapshot.symbol, SkewSnapshot.ts.desc())
    )
    # Keep just the latest (symbol, ts) per name.
    rows = session.execute(sub).all()
    if not rows:
        return pd.DataFrame()
    latest_per_symbol: dict[str, date] = {}
    for sym, ts in rows:
        latest_per_symbol.setdefault(sym, ts)

    out_rows: list[tuple] = []
    for sym, ts in latest_per_symbol.items():
        rec = session.execute(
            select(
                SkewSnapshot.symbol,
                SkewSnapshot.ts,
                SkewSnapshot.atm_iv,
                SkewSnapshot.rr_25d,
                SkewSnapshot.rr_10d,
                SkewSnapshot.bf_25d,
                SkewSnapshot.rr_25d_pctile_63d,
                SkewSnapshot.rr_25d_pctile_252d,
                SkewSnapshot.front_back_rr_slope,
                SkewSnapshot.vix_beta_60d,
                SkewSnapshot.rr_25d_abnormal,
                SkewSnapshot.shift_slide_label,
                SkewSnapshot.label,
            ).where(
                SkewSnapshot.symbol == sym,
                SkewSnapshot.ts == ts,
                SkewSnapshot.horizon_dte == horizon_dte,
            )
        ).first()
        if rec is not None:
            out_rows.append(rec)

    cols = [
        "symbol", "ts", "atm_iv", "rr_25d", "rr_10d", "bf_25d",
        "rr_25d_pctile_63d", "rr_25d_pctile_252d",
        "front_back_rr_slope", "vix_beta_60d", "rr_25d_abnormal",
        "shift_slide_label", "label",
    ]
    df = pd.DataFrame(out_rows, columns=cols)
    return df.sort_values(
        by="rr_25d_pctile_252d", ascending=True, na_position="last"
    ).reset_index(drop=True)


def per_name_timeseries(
    session: Session,
    symbol: str,
    *,
    horizon_dte: int = 30,
    lookback_days: int = 365,
) -> pd.DataFrame:
    """Per-name RR + ATM + price time series for the MU-style chart."""
    start = date.today() - timedelta(days=lookback_days)
    skew_rows = session.execute(
        select(
            SkewSnapshot.ts,
            SkewSnapshot.atm_iv,
            SkewSnapshot.rr_25d,
            SkewSnapshot.rr_25d_pctile_252d,
            SkewSnapshot.shift_slide_label,
        ).where(
            SkewSnapshot.symbol == symbol,
            SkewSnapshot.horizon_dte == horizon_dte,
            SkewSnapshot.ts >= start,
        ).order_by(SkewSnapshot.ts.asc())
    ).all()
    if not skew_rows:
        return pd.DataFrame()
    skew_df = pd.DataFrame(
        skew_rows,
        columns=["ts", "atm_iv", "rr_25d", "rr_25d_pctile_252d", "shift_slide_label"],
    )
    quote_rows = session.execute(
        select(QuoteDaily.date, QuoteDaily.close)
        .where(QuoteDaily.symbol == symbol, QuoteDaily.date >= start)
        .order_by(QuoteDaily.date.asc())
    ).all()
    quote_df = pd.DataFrame(quote_rows, columns=["ts", "close"]) if quote_rows else pd.DataFrame()
    if quote_df.empty:
        return skew_df
    skew_df["ts"] = pd.to_datetime(skew_df["ts"])
    quote_df["ts"] = pd.to_datetime(quote_df["ts"])
    return skew_df.merge(quote_df, on="ts", how="left")


def per_name_rr_band(
    session: Session,
    symbol: str,
    *,
    horizon_dte: int = 30,
    window: int = 63,
) -> pd.DataFrame:
    """Trailing min/max/mean RR band for the chart's shaded region.

    Pure pandas — fetches the time series, then rolls.
    """
    ts_df = per_name_timeseries(
        session, symbol, horizon_dte=horizon_dte, lookback_days=window * 2
    )
    if ts_df.empty or "rr_25d" not in ts_df.columns:
        return ts_df
    rr = ts_df["rr_25d"]
    ts_df["rr_min"] = rr.rolling(window=window, min_periods=window // 2).min()
    ts_df["rr_max"] = rr.rolling(window=window, min_periods=window // 2).max()
    ts_df["rr_mean"] = rr.rolling(window=window, min_periods=window // 2).mean()
    return ts_df


# ── Index time series ──────────────────────────────────────────────────


def index_timeseries(
    session: Session, *, lookback_days: int = 365
) -> pd.DataFrame:
    """Long-form index-skew rows over the lookback window."""
    start = date.today() - timedelta(days=lookback_days)
    rows = session.execute(
        select(
            IndexSkewDaily.date,
            IndexSkewDaily.cboe_skew,
            IndexSkewDaily.sdex,
            IndexSkewDaily.spx_rr_25d_30d,
            IndexSkewDaily.spx_rr_pctile_252d,
            IndexSkewDaily.sdex_pctile_252d,
            IndexSkewDaily.vvix,
            IndexSkewDaily.vix_call_skew_25d,
            IndexSkewDaily.vix_call_oi_share,
            IndexSkewDaily.vix_tail_hedging_score,
        ).where(IndexSkewDaily.date >= start).order_by(IndexSkewDaily.date.asc())
    ).all()
    if not rows:
        return pd.DataFrame()
    cols = [
        "date", "cboe_skew", "sdex", "spx_rr_25d_30d",
        "spx_rr_pctile_252d", "sdex_pctile_252d",
        "vvix", "vix_call_skew_25d", "vix_call_oi_share", "vix_tail_hedging_score",
    ]
    df = pd.DataFrame(rows, columns=cols)
    df["date"] = pd.to_datetime(df["date"])
    return df


# ── VIX-options view ───────────────────────────────────────────────────


def vix_options_today(
    session: Session, *, as_of: date | None = None
) -> pd.DataFrame:
    """Today's VIX options chain rows — for the call-wing IV + OI distribution view."""
    if as_of is None:
        as_of = session.execute(
            select(VixOptionsChain.ts).order_by(VixOptionsChain.ts.desc()).limit(1)
        ).scalar_one_or_none()
    if as_of is None:
        return pd.DataFrame()
    rows = session.execute(
        select(
            VixOptionsChain.expiration,
            VixOptionsChain.strike,
            VixOptionsChain.opt_kind,
            VixOptionsChain.delta,
            VixOptionsChain.iv,
            VixOptionsChain.oi,
            VixOptionsChain.volume,
        ).where(VixOptionsChain.ts == as_of)
    ).all()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(
        rows,
        columns=["expiration", "strike", "opt_kind", "delta", "iv", "oi", "volume"],
    )
