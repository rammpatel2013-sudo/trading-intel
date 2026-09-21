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
    "legged_notional",
    "leg_share",
]

# Index roots are ranked on the same notional scale as single names, so one blended
# table is always ~all index. Worse, the biggest index prints are BOXES (deep-ITM
# call + deep-ITM put, same expiry) -- interest-rate financing with no directional
# content -- so they must be dropped, not merely separated.
_INDEX_ROOTS = frozenset({"SPX", "SPXW", "XSP", "SPY", "QQQ", "IWM", "NDX", "RUT", "VIX"})
_DEEP_DELTA = 0.85      # |delta| at/above this = deep ITM, ~no convexity
_LEAP_DELTA = 0.70      # lower bar when it is also long-dated
_LEAP_DTE = 365
_DEEP_MONEY = 0.10      # fallback when avg_delta is null: 10% ITM
_ITM_PAD = 0.02         # a leg counts as ITM for box-pairing at >=2% in the money
_PAIR_MIN_NOTIONAL = 5e7  # both legs must be institutional size to call it a box
_MULTILEG_SHARE = 0.50    # >=50% of premium printed inside a leg_group -> packaged


def _deep_itm(cp, delta, dte, spot, strike) -> bool:
    """Lone deep-ITM / LEAP financing print. Mirrors tas_capture_job._is_financing,
    but reads the ROLL-UP columns -- tas_daily_contract has no is_financing column,
    so the raw tape tag never reaches the report."""
    ad = abs(delta) if delta is not None and pd.notna(delta) else None
    if ad is not None:
        if ad >= _DEEP_DELTA:
            return True
        return bool(ad >= _LEAP_DELTA and dte is not None and dte >= _LEAP_DTE)
    if spot and strike and pd.notna(spot) and pd.notna(strike) and spot > 0:
        if cp == "C":
            return strike <= spot * (1 - _DEEP_MONEY)
        if cp == "P":
            return strike >= spot * (1 + _DEEP_MONEY)
    return False


def _itm(cp, spot, strike, pad: float = _ITM_PAD) -> bool:
    """Simply in-the-money by `pad`. Used for STRUCTURAL box detection."""
    if not (spot and strike) or pd.isna(spot) or pd.isna(strike) or spot <= 0:
        return False
    return strike <= spot * (1 - pad) if cp == "C" else strike >= spot * (1 + pad)


def classify_contracts(df: pd.DataFrame, *, as_of: date,
                       index_roots: frozenset[str] | None = None) -> pd.DataFrame:
    """Tag lifecycle rows is_index / is_financing / is_box_leg. Pure.

    Box detection is STRUCTURAL, not delta-thresholded, on purpose: an SPX 8000 put
    against a 7,700 spot is only ~0.68 delta, so a deep-ITM delta gate misses the put
    leg of every box and leaves it looking like genuine one-sided put flow. The real
    signature is a big ITM CALL and a big ITM PUT on the SAME expiry.
    """
    if df is None or df.empty:
        return df
    roots = index_roots or _INDEX_ROOTS
    out = df.copy()
    out["is_index"] = out["root"].astype(str).str.upper().isin(roots)

    def _dte(e):
        try:
            return (pd.to_datetime(e).date() - as_of).days
        except Exception:
            return None

    dtes = out["expiry"].map(_dte)
    out["is_financing"] = [
        _deep_itm(cp, dl, dt, sp, st)
        for cp, dl, dt, sp, st in zip(
            out["cp"], out.get("avg_delta"), dtes, out.get("avg_spot"), out["strike"]
        )
    ]

    itm_mask = pd.Series(
        [_itm(c, s, k) for c, s, k in zip(out["cp"], out.get("avg_spot"), out["strike"])],
        index=out.index,
    )
    out["is_box_leg"] = False
    big = out[out["total_notional"].fillna(0.0) >= _PAIR_MIN_NOTIONAL]
    for (root, exp), g in big.groupby(["root", "expiry"], dropna=False):
        gm = itm_mask.reindex(g.index).fillna(False)
        has_c = bool(((g["cp"] == "C") & gm).any())
        has_p = bool(((g["cp"] == "P") & gm).any())
        if has_c and has_p:
            out.loc[(out["root"] == root) & (out["expiry"] == exp) & itm_mask,
                    "is_box_leg"] = True
    out["is_financing"] = out["is_financing"] | out["is_box_leg"]

    # Scope the whole filter to INDEX roots. A box spread is an interest-rate
    # product -- single names have no financing market that trades at this size --
    # whereas a deep-ITM single-name call is usually STOCK REPLACEMENT, i.e. exactly
    # the directional build this report exists to surface. Suppressing those would
    # hide good flow to fix an index problem.
    out["is_financing"] = out["is_financing"] & out["is_index"]
    out["is_box_leg"] = out["is_box_leg"] & out["is_index"]

    legs = (
        pd.to_numeric(out["leg_share"], errors="coerce").fillna(0.0)
        if "leg_share" in out.columns
        else pd.Series(0.0, index=out.index)
    )
    dte_s = pd.Series(list(dtes), index=out.index)

    def _label(i) -> str:
        if bool(out.at[i, "is_box_leg"]):
            return "box"
        if bool(out.at[i, "is_financing"]):
            d = dte_s.at[i]
            return "LEAP" if (d is not None and pd.notna(d) and d >= _LEAP_DTE) else "deep-ITM"
        if legs.at[i] >= _MULTILEG_SHARE:
            return "multi-leg"
        return ""

    out["structure"] = [_label(i) for i in out.index]
    return out


def split_contracts(df: pd.DataFrame) -> dict[str, Any]:
    """Split classified rows into index / equity, financing suppressed from both."""
    if df is None or df.empty:
        return {"equity": df, "index": df, "n_financing": 0, "n_boxes": 0}
    live = df[~df["is_financing"]]
    return {
        "equity": live[~live["is_index"]].reset_index(drop=True),
        "index": live[live["is_index"]].reset_index(drop=True),
        # surfaced, not hidden: a filter you cannot inspect is a filter you
        # cannot trust, and these rows are real money worth eyeballing
        "structures": df[df["is_financing"]]
        .sort_values("total_notional", ascending=False)
        .reset_index(drop=True),
        "n_financing": int(df["is_financing"].sum()),
        "n_boxes": int(df["is_box_leg"].sum()),
    }


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
    for col in ("total_notional", "net_dollar_delta", "total_size", "spot",
                "avg_delta", "legged_notional"):
        if col not in df.columns:
            df[col] = None
        df[col] = pd.to_numeric(df.get(col), errors="coerce")

    g = df.groupby(["root", "expiry", "strike", "cp"], dropna=False)
    out = g.agg(
        days_seen=("trade_date", "nunique"),
        total_notional=("total_notional", "sum"),
        cum_net_dollar_delta=("net_dollar_delta", "sum"),
        total_size=("total_size", "sum"),
        avg_spot=("spot", "mean"),
        avg_delta=("avg_delta", "mean"),
        legged_notional=("legged_notional", "sum"),
        first_date=("trade_date", "min"),
        last_date=("trade_date", "max"),
    ).reset_index()

    out = out[
        (out["total_notional"].fillna(0.0) >= min_notional) & (out["days_seen"] >= max(1, min_days))
    ].copy()
    if out.empty:
        return pd.DataFrame(columns=_LIFECYCLE_COLS)

    # share of this contract's premium that printed inside a multi-leg structure
    _tot = pd.to_numeric(out["total_notional"], errors="coerce").astype(float)
    _leg = pd.to_numeric(out["legged_notional"], errors="coerce").astype(float).fillna(0.0)
    out["leg_share"] = (_leg / _tot.where(_tot > 0)).fillna(0.0).clip(0.0, 1.0)

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
                "legged_notional": getattr(r, "legged_notional", None),
            }
            for r in rows
        ]
    )


def watchlist_symbols(session: Session, settings: object | None = None) -> set[str]:
    """The monitored universe = active ``tickers`` rows ∪ config WATCHLIST.

    The tape is market-wide, so without this the report answers "what moved
    anywhere" rather than "what moved in the names I actually follow".
    """
    out: set[str] = set()
    try:
        from trading_intel.memory.models import Ticker

        rows = session.execute(
            select(Ticker.symbol).where(Ticker.is_active.is_(True))
        ).scalars()
        out |= {str(x).upper() for x in rows if x}
    except Exception:  # pragma: no cover - table shape drift
        pass
    try:
        if settings is None:
            from trading_intel.config import get_settings

            settings = get_settings()
        out |= {str(x).upper() for x in getattr(settings, "watchlist_symbols", [])}
    except Exception:  # pragma: no cover
        pass
    return out


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



def _trend_ends(side: pd.DataFrame, top: int) -> pd.DataFrame:
    """Top accumulators AND top distributors for one side.

    ``head(top)`` alone keeps only the strongest buyers, so the distribution
    table for that side renders empty no matter how heavy the selling was. The
    frame is score-sorted, so both ends must be kept.
    """
    if side is None or side.empty:
        return side if side is not None else pd.DataFrame()
    if len(side) <= 2 * top:
        return side
    return pd.concat([side.head(top), side.tail(top)])


def _trend_side(trend: pd.DataFrame, *, index_side: bool) -> pd.DataFrame:
    """Split the accumulation-trend frame into index vs single-name roots."""
    if trend is None or trend.empty or "root" not in trend.columns:
        return trend if trend is not None else pd.DataFrame()
    mask = trend["root"].astype(str).str.upper().isin(_INDEX_ROOTS)
    return trend[mask] if index_side else trend[~mask]


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
    wide = contract_lifecycle(
        contracts, min_notional=min_notional, top=max(top * 10, 250)
    )
    wide = classify_contracts(wide, as_of=end)
    split = split_contracts(wide)
    lifecycle = wide.head(top)

    wl = watchlist_symbols(session)
    if wl and not wide.empty:
        roots = wide["root"].astype(str).str.upper()
        wl_contracts = wide[roots.isin(wl) & ~wide["is_financing"]].head(top)
    else:
        wl_contracts = wide.head(0)
    if wl and not trend.empty:
        wl_trend = trend[trend["root"].astype(str).str.upper().isin(wl)].head(top)
    else:
        wl_trend = trend.head(0)
    churn = new_vs_fading(
        daily, recent_days=recent_days, prior_days=prior_days, min_notional=min_notional
    )
    found = not (trend.empty and lifecycle.empty)
    return {
        "as_of": end.isoformat(),
        "lookback_days": lookback_days,
        "recent_days": recent_days,
        "trend": _records(trend),
        # Index and single-name tape are different animals: one is hedging/overlay
        # flow on a handful of roots, the other is directional single-stock
        # positioning. Ranking them in one table lets SPX/SPY dominate every
        # leaderboard, so the trend frame is split the same way contracts are.
        "trend_equity": _records(_trend_ends(_trend_side(trend, index_side=False), top)),
        "trend_index": _records(_trend_ends(_trend_side(trend, index_side=True), top)),
        "contracts": _records(lifecycle),
        "contracts_equity": _records(split["equity"].head(top)),
        "contracts_index": _records(split["index"].head(top)),
        "contracts_structures": _records(split["structures"].head(top)),
        "contracts_watchlist": _records(wl_contracts),
        "trend_watchlist": _records(wl_trend),
        "watchlist_size": len(wl),
        "contracts_suppressed": {
            "financing": split["n_financing"], "boxes": split["n_boxes"]
        },
        "new": churn["new"],
        "fading": churn["fading"],
        "count": {
            "trend": len(trend),
            "contracts": len(lifecycle),
            "contracts_equity": int(len(split["equity"])),
            "contracts_index": int(len(split["index"])),
        },
        "found": found,
    }
