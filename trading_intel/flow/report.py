"""Longitudinal option-flow insight over the durable daily roll-up tables.

The point-in-time board lives in ``flow/scorecard.py`` (one accum score per name
over a window). This module adds the *trend* layer the EOD Flow Report needs,
reading only the durable tables that survive the 30-day raw-print prune —
``tas_daily_flow`` (per name) and ``tas_daily_contract`` (per contract):

  - ``accumulation_trend``  per-name recent-vs-prior accum score, net-$delta
                            change and a signed net-buy/sell streak (who is
                            building vs bailing, and is it fresh or fading)
  - ``contract_lifecycle``  per (root, expiry, strike, cp) build over the window:
                            notional, days seen, cumulative net $delta, moneyness
  - ``new_vs_fading``       names newly ON vs dropping OFF the accumulation board

Pure ``df -> df`` transforms (DB reads live in the loaders) so they unit-test
without a database, and DESCRIPTIVE only (FlashAlpha rule 4) — nothing here
writes ``signals``. ``build_flow_report`` is the single call the MCP tool / HTML
report use; it returns JSON-native records.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.flow.scorecard import load_daily_flow, score_names
from trading_intel.memory.models import TasDailyContract

_ACCUM_CUTOFF = 20.0  # accum_score >= this counts as "on the accumulation board"

_TREND_COLS = [
    "root",
    "days_observed",
    "recent_score",
    "prior_score",
    "score_delta",
    "net_dollar_delta",
    "streak_days",
    "label",
]

_LIFECYCLE_COLS = [
    "root",
    "expiry",
    "strike",
    "cp",
    "days_seen",
    "total_notional",
    "cum_net_dollar_delta",
    "total_size",
    "avg_spot",
    "avg_delta",
    "first_date",
    "last_date",
    "build_side",
]


def _as_day(values: pd.Series) -> pd.Series:
    """Normalise a date-ish column to python ``date`` (drops time-of-day)."""
    return pd.to_datetime(values, errors="coerce").dt.date


def _split_windows(dates: pd.Series, recent_days: int, prior_days: int) -> tuple[set, set]:
    """Most-recent ``recent_days`` distinct sessions and the ``prior_days`` before."""
    uniq = sorted({d for d in dates if d is not None})
    recent = set(uniq[-recent_days:]) if recent_days > 0 else set()
    cut = len(uniq) - recent_days
    prior = set(uniq[max(0, cut - prior_days) : cut]) if cut > 0 else set()
    return recent, prior


def _net_buy_streaks(daily: pd.DataFrame) -> pd.DataFrame:
    """Signed trailing streak of net-buy(+) / net-sell(-) days per root.

    +3 = the last three sessions were net buying; -2 = last two net selling; the
    run breaks on a flat (net_dollar_delta == 0) or a sign flip.
    """
    rows: list[dict[str, Any]] = []
    for root, g in daily.sort_values("trade_date").groupby("root"):
        streak = 0
        for v in reversed(list(g["net_dollar_delta"])):
            s = 1 if v > 0 else (-1 if v < 0 else 0)
            if s == 0 or (streak != 0 and (s > 0) != (streak > 0)):
                break
            streak += s
        rows.append({"root": root, "streak_days": streak})
    return pd.DataFrame(rows, columns=["root", "streak_days"])


def accumulation_trend(
    daily: pd.DataFrame,
    *,
    recent_days: int = 5,
    prior_days: int = 5,
    min_notional: float = 0.0,
    min_days: int = 1,
) -> pd.DataFrame:
    """Per-name recent-vs-prior accumulation trend from ``tas_daily_flow`` rows.

    ``daily`` is the long per-name/day frame (cols: ``root, trade_date,
    total_notional, buy_notional, sell_notional, net_dollar_delta,
    gross_dollar_delta``). Scores the most-recent ``recent_days`` sessions and the
    ``prior_days`` before them with ``score_names`` and reports the shift plus a
    signed net-buy streak. Ranked by ``recent_score`` descending.
    """
    if daily is None or daily.empty:
        return pd.DataFrame(columns=_TREND_COLS)

    df = daily.copy()
    df["trade_date"] = _as_day(df["trade_date"])
    recent_dates, prior_dates = _split_windows(df["trade_date"], recent_days, prior_days)

    recent = score_names(
        df[df["trade_date"].isin(recent_dates)], min_notional=min_notional, min_days=1
    )
    prior = score_names(df[df["trade_date"].isin(prior_dates)], min_notional=0.0, min_days=1)

    full_days = df.groupby("root")["trade_date"].nunique().rename("days_observed").reset_index()
    full_net = df.groupby("root")["net_dollar_delta"].sum().rename("net_dollar_delta").reset_index()

    out = recent[["root", "accum_score", "label"]].rename(columns={"accum_score": "recent_score"})
    out = out.merge(
        prior[["root", "accum_score"]].rename(columns={"accum_score": "prior_score"}),
        on="root",
        how="left",
    )
    out = out.merge(full_days, on="root", how="left").merge(full_net, on="root", how="left")
    out = out.merge(_net_buy_streaks(df), on="root", how="left")

    out["prior_score"] = out["prior_score"].fillna(0.0)
    out["streak_days"] = out["streak_days"].fillna(0).astype(int)
    out["days_observed"] = out["days_observed"].fillna(0).astype(int)
    out["score_delta"] = (out["recent_score"] - out["prior_score"]).round(1)

    out = out[out["days_observed"] >= max(1, min_days)]
    if out.empty:
        return pd.DataFrame(columns=_TREND_COLS)
    return out[_TREND_COLS].sort_values("recent_score", ascending=False).reset_index(drop=True)


def contract_lifecycle(
    contracts: pd.DataFrame,
    *,
    min_notional: float = 0.0,
    min_days: int = 1,
    top: int = 25,
) -> pd.DataFrame:
    """Per-(root, expiry, strike, cp) build over the window from ``tas_daily_contract``.

    ``contracts`` long frame (cols: ``root, trade_date, expiry, strike, cp,
    total_notional, net_dollar_delta, total_size, spot, avg_delta``). Aggregates
    each contract across the window; ``build_side`` = accumulation / distribution /
    neutral from the cumulative signed $delta. Ranked by total notional.
    """
    if contracts is None or contracts.empty:
        return pd.DataFrame(columns=_LIFECYCLE_COLS)

    df = contracts.copy()
    df["trade_date"] = _as_day(df["trade_date"])
    for col in ("total_notional", "net_dollar_delta", "total_size", "spot", "avg_delta"):
        df[col] = pd.to_numeric(df.get(col), errors="coerce")

    g = df.groupby(["root", "expiry", "strike", "cp"], dropna=False)
    out = g.agg(
        days_seen=("trade_date", "nunique"),
        total_notional=("total_notional", "sum"),
        cum_net_dollar_delta=("net_dollar_delta", "sum"),
        total_size=("total_size", "sum"),
        avg_spot=("spot", "mean"),
        avg_delta=("avg_delta", "mean"),
        first_date=("trade_date", "min"),
        last_date=("trade_date", "max"),
    ).reset_index()

    out = out[
        (out["total_notional"].fillna(0.0) >= min_notional) & (out["days_seen"] >= max(1, min_days))
    ].copy()
    if out.empty:
        return pd.DataFrame(columns=_LIFECYCLE_COLS)

    out["build_side"] = out["cum_net_dollar_delta"].map(
        lambda v: "accumulation" if v > 0 else ("distribution" if v < 0 else "neutral")
    )
    out = out.sort_values("total_notional", ascending=False).reset_index(drop=True)
    return out[_LIFECYCLE_COLS].head(max(1, top))


def new_vs_fading(
    daily: pd.DataFrame,
    *,
    recent_days: int = 5,
    prior_days: int = 5,
    cutoff: float = _ACCUM_CUTOFF,
    min_notional: float = 0.0,
) -> dict[str, list[str]]:
    """Names newly ON vs dropping OFF the accumulation board.

    ``new`` = accumulating (accum_score >= ``cutoff``) in the recent sub-window
    but not in the prior one; ``fading`` = accumulating in the prior window but no
    longer in the recent one.
    """
    if daily is None or daily.empty:
        return {"new": [], "fading": []}
    df = daily.copy()
    df["trade_date"] = _as_day(df["trade_date"])
    recent_dates, prior_dates = _split_windows(df["trade_date"], recent_days, prior_days)
    recent = score_names(df[df["trade_date"].isin(recent_dates)], min_notional=min_notional)
    prior = score_names(df[df["trade_date"].isin(prior_dates)], min_notional=min_notional)
    r_acc = set(recent.loc[recent["accum_score"] >= cutoff, "root"])
    p_acc = set(prior.loc[prior["accum_score"] >= cutoff, "root"])
    return {"new": sorted(r_acc - p_acc), "fading": sorted(p_acc - r_acc)}


# ── DB loaders ─────────────────────────────────────────────────────────


def load_daily_contract(
    session: Session, *, lookback_days: int = 21, end_date: date | None = None
) -> pd.DataFrame:
    """Load ``tas_daily_contract`` rows for the last ``lookback_days`` as a DataFrame."""
    end = end_date or date.today()
    start = end - timedelta(days=lookback_days)
    rows = list(
        session.execute(
            select(TasDailyContract)
            .where(
                TasDailyContract.trade_date > start,
                TasDailyContract.trade_date <= end,
            )
            .order_by(TasDailyContract.trade_date)
        ).scalars()
    )
    return pd.DataFrame(
        [
            {
                "root": r.root,
                "trade_date": r.trade_date,
                "expiry": r.expiry,
                "strike": r.strike,
                "cp": r.cp,
                "total_notional": r.total_notional,
                "net_dollar_delta": r.net_dollar_delta,
                "total_size": r.total_size,
                "spot": r.spot,
                "avg_delta": r.avg_delta,
            }
            for r in rows
        ]
    )


# ── assembly ───────────────────────────────────────────────────────────


def _num(v: object) -> str | float | int | None:
    """JSON-safe scalar: dates -> ISO, NaN/NA -> None, numpy -> python number."""
    if isinstance(v, date):  # datetime.date/datetime (expiry, first/last_date)
        return v.isoformat()
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, str):
        return v
    try:
        f = float(v)
        return int(f) if f.is_integer() else round(f, 4)
    except (TypeError, ValueError):
        return str(v)


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    return [{k: _num(v) for k, v in row.items()} for row in df.to_dict("records")]


def build_flow_report(
    session: Session,
    *,
    lookback_days: int = 21,
    recent_days: int = 5,
    min_notional: float = 0.0,
    min_days: int = 2,
    top: int = 25,
    end_date: date | None = None,
) -> dict[str, Any]:
    """Assemble the longitudinal flow report off the durable roll-up tables.

    Returns JSON-native sections: ``trend`` (per-name recent-vs-prior),
    ``contracts`` (per-contract lifecycle), and ``new`` / ``fading`` name lists.
    """
    end = end_date or date.today()
    prior_days = max(1, lookback_days - recent_days)
    daily = load_daily_flow(session, lookback_days=lookback_days, end_date=end)
    contracts = load_daily_contract(session, lookback_days=lookback_days, end_date=end)

    trend = accumulation_trend(
        daily,
        recent_days=recent_days,
        prior_days=prior_days,
        min_notional=min_notional,
        min_days=min_days,
    )
    lifecycle = contract_lifecycle(contracts, min_notional=min_notional, top=top)
    churn = new_vs_fading(
        daily, recent_days=recent_days, prior_days=prior_days, min_notional=min_notional
    )
    found = not (trend.empty and lifecycle.empty)
    return {
        "as_of": end.isoformat(),
        "lookback_days": lookback_days,
        "recent_days": recent_days,
        "trend": _records(trend),
        "contracts": _records(lifecycle),
        "new": churn["new"],
        "fading": churn["fading"],
        "count": {"trend": len(trend), "contracts": len(lifecycle)},
        "found": found,
    }
