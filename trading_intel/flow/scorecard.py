"""Multi-day accumulation / distribution scorecard from the daily flow roll-up.

Reads ``tas_daily_flow`` (the durable per-name daily aggregate that survives the
30-day raw-print prune) over a lookback window and scores each name on whether the
option tape shows persistent *accumulation* (net buying, consistent day over day)
or *distribution* (net selling). The score is in ``[-100, +100]`` — positive =
accumulation, negative = distribution — and is built from three descriptive
ingredients:

  - ``net_delta_norm``  signed net $delta / gross $delta  (directional cleanliness)
  - ``persistence``     (days net-buy minus days net-sell) / days  (consistency)
  - ``buy_tilt``        (buy minus sell premium) / total premium  (aggressor lean)

This is a DESCRIPTIVE ranking to guide where to look — NOT a trade signal and NOT
written to the ``signals`` table (FlashAlpha rule 4). Pure scoring (``score_names``)
is separated from the DB read (``load_daily_flow``) so it is unit-tested without a
database.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.memory.models import TasDailyFlow

# Composite weights (sum = 1.0). Tunable; documented in docs/playbooks/tas_pipeline.md.
_W_NET_DELTA = 0.45
_W_PERSISTENCE = 0.35
_W_BUY_TILT = 0.20

_ACCUM_CUTOFF = 20.0  # score >= this -> "accumulation"
_DISTRIB_CUTOFF = -20.0  # score <= this -> "distribution"

_SCORE_COLS = [
    "root",
    "days_observed",
    "total_notional",
    "avg_daily_notional",
    "net_dollar_delta",
    "buy_notional",
    "sell_notional",
    "days_net_buy",
    "days_net_sell",
    "persistence",
    "buy_tilt",
    "net_delta_norm",
    "accum_score",
    "label",
]


def _label(score: float) -> str:
    if score >= _ACCUM_CUTOFF:
        return "accumulation"
    if score <= _DISTRIB_CUTOFF:
        return "distribution"
    return "neutral"


def score_names(
    daily: pd.DataFrame, *, min_notional: float = 0.0, min_days: int = 1
) -> pd.DataFrame:
    """Score per-name accumulation/distribution from daily roll-up rows.

    ``daily`` needs columns: ``root, trade_date, total_notional, buy_notional,
    sell_notional, net_dollar_delta, gross_dollar_delta``. Ranked by ``accum_score``
    descending (strongest accumulation first; strongest distribution last).

    ``min_days`` drops names seen on fewer than that many sessions — the score
    saturates on a single big day, so a 2-3 day floor keeps one-off blocks from
    topping the board. ``min_notional`` drops thin names by total premium.
    """
    if daily is None or daily.empty:
        return pd.DataFrame(columns=_SCORE_COLS)

    df = daily.copy()
    for col in (
        "total_notional",
        "buy_notional",
        "sell_notional",
        "net_dollar_delta",
        "gross_dollar_delta",
    ):
        df[col] = pd.to_numeric(df.get(col), errors="coerce").fillna(0.0)

    g = df.assign(
        net_buy_day=(df["net_dollar_delta"] > 0).astype(int),
        net_sell_day=(df["net_dollar_delta"] < 0).astype(int),
    ).groupby("root")
    out = g.agg(
        days_observed=("trade_date", "nunique"),
        total_notional=("total_notional", "sum"),
        buy_notional=("buy_notional", "sum"),
        sell_notional=("sell_notional", "sum"),
        net_dollar_delta=("net_dollar_delta", "sum"),
        gross_dollar_delta=("gross_dollar_delta", "sum"),
        days_net_buy=("net_buy_day", "sum"),
        days_net_sell=("net_sell_day", "sum"),
    ).reset_index()

    out = out[
        (out["total_notional"] >= min_notional) & (out["days_observed"] >= max(1, min_days))
    ].copy()
    if out.empty:
        return pd.DataFrame(columns=_SCORE_COLS)

    days = out["days_observed"].clip(lower=1)
    out["avg_daily_notional"] = out["total_notional"] / days
    out["persistence"] = (out["days_net_buy"] - out["days_net_sell"]) / days
    prem = (out["buy_notional"] + out["sell_notional"]).where(
        out["buy_notional"] + out["sell_notional"] > 0, 1.0
    )
    out["buy_tilt"] = (out["buy_notional"] - out["sell_notional"]) / prem
    gross = out["gross_dollar_delta"].where(out["gross_dollar_delta"] > 0, 1.0)
    out["net_delta_norm"] = (out["net_dollar_delta"] / gross).clip(-1.0, 1.0)

    out["accum_score"] = (
        100.0
        * (
            _W_NET_DELTA * out["net_delta_norm"]
            + _W_PERSISTENCE * out["persistence"]
            + _W_BUY_TILT * out["buy_tilt"]
        )
    ).round(1)
    out["label"] = out["accum_score"].map(_label)
    return out[_SCORE_COLS].sort_values("accum_score", ascending=False).reset_index(drop=True)


def load_daily_flow(
    session: Session, *, lookback_days: int = 20, end_date: date | None = None
) -> pd.DataFrame:
    """Load ``tas_daily_flow`` rows for the last ``lookback_days`` as a DataFrame."""
    end = end_date or date.today()
    start = end - timedelta(days=lookback_days)
    rows = list(
        session.execute(
            select(TasDailyFlow)
            .where(TasDailyFlow.trade_date > start, TasDailyFlow.trade_date <= end)
            .order_by(TasDailyFlow.trade_date)
        ).scalars()
    )
    return pd.DataFrame(
        [
            {
                "root": r.root,
                "trade_date": r.trade_date,
                "total_notional": r.total_notional,
                "buy_notional": r.buy_notional,
                "sell_notional": r.sell_notional,
                "net_dollar_delta": r.net_dollar_delta,
                "gross_dollar_delta": r.gross_dollar_delta,
            }
            for r in rows
        ]
    )


def build_scorecard(
    session: Session,
    *,
    lookback_days: int = 20,
    min_notional: float = 0.0,
    min_days: int = 1,
    end_date: date | None = None,
) -> pd.DataFrame:
    """Load the window and score it — the one call the script/MCP tool use."""
    daily = load_daily_flow(session, lookback_days=lookback_days, end_date=end_date)
    return score_names(daily, min_notional=min_notional, min_days=min_days)
