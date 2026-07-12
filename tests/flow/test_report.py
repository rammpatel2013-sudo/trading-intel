"""Unit tests for the longitudinal flow report (``flow/report.py``).

Pure functions are exercised with synthetic DataFrames; ``build_flow_report`` is
run against an in-memory SQLite session seeded with ``tas_daily_flow`` +
``tas_daily_contract`` rows. No Convex, no Postgres.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.flow.report import (
    accumulation_trend,
    build_flow_report,
    contract_lifecycle,
    new_vs_fading,
)
from trading_intel.memory.models import TasDailyContract, TasDailyFlow

_BASE = date(2026, 6, 1)


def _flow_row(root: str, i: int, net: float, buy: float, sell: float) -> TasDailyFlow:
    return TasDailyFlow(
        trade_date=_BASE + timedelta(days=i),
        root=root,
        total_notional=buy + sell,
        buy_notional=buy,
        sell_notional=sell,
        net_dollar_delta=net,
        gross_dollar_delta=max(abs(net), 1.0),
    )


def _seed_flow() -> list[TasDailyFlow]:
    rows: list[TasDailyFlow] = []
    for i in range(10):
        recent = i >= 5
        # AAA: persistent accumulator every day
        rows.append(_flow_row("AAA", i, +100.0, 200.0, 0.0))
        # BBB: persistent distributor every day
        rows.append(_flow_row("BBB", i, -100.0, 0.0, 200.0))
        # CCC: accumulates in the prior window, distributes in the recent -> fading
        rows.append(
            _flow_row(
                "CCC",
                i,
                -100.0 if recent else 100.0,
                0.0 if recent else 200.0,
                200.0 if recent else 0.0,
            )
        )
        # DDD: neutral prior, accumulates recently -> new
        rows.append(
            _flow_row(
                "DDD",
                i,
                100.0 if recent else 0.0,
                200.0 if recent else 100.0,
                0.0 if recent else 100.0,
            )
        )
    return rows


def _seed_contracts() -> list[TasDailyContract]:
    rows: list[TasDailyContract] = []
    for i in range(6):
        rows.append(
            TasDailyContract(
                trade_date=_BASE + timedelta(days=i),
                root="AAA",
                expiry=date(2026, 9, 18),
                strike=250.0,
                cp="C",
                n_prints=3,
                total_notional=500_000.0,
                total_size=100,
                avg_price=5.0,
                spot=240.0,
                avg_delta=0.35,
                net_dollar_delta=400_000.0,
            )
        )
    return rows


def _daily_df() -> pd.DataFrame:
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
            for r in _seed_flow()
        ]
    )


def test_accumulation_trend_scores_and_streak() -> None:
    out = accumulation_trend(_daily_df(), recent_days=5, prior_days=5, min_days=2)
    by = out.set_index("root")
    assert by.loc["AAA", "recent_score"] == pytest.approx(100.0)
    assert by.loc["AAA", "label"] == "accumulation"
    assert by.loc["AAA", "streak_days"] == 10  # ten straight net-buy days
    assert by.loc["BBB", "label"] == "distribution"
    assert by.loc["BBB", "streak_days"] == -10
    # ranked strongest accumulation first
    assert out.iloc[0]["root"] == "AAA"


def test_new_vs_fading() -> None:
    churn = new_vs_fading(_daily_df(), recent_days=5, prior_days=5)
    assert "DDD" in churn["new"]
    assert "CCC" in churn["fading"]
    assert "AAA" not in churn["new"]  # accumulating in both windows, not "new"


def test_contract_lifecycle() -> None:
    df = pd.DataFrame(
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
            for r in _seed_contracts()
        ]
    )
    out = contract_lifecycle(df, min_notional=0.0, top=10)
    row = out.iloc[0]
    assert row["root"] == "AAA" and row["cp"] == "C" and row["strike"] == 250.0
    assert row["days_seen"] == 6
    assert row["build_side"] == "accumulation"
    assert row["total_notional"] == pytest.approx(3_000_000.0)


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite://")
    TasDailyFlow.__table__.create(engine)
    TasDailyContract.__table__.create(engine)
    with Session(engine) as s:
        yield s


def test_build_flow_report_end_to_end(session: Session) -> None:
    session.add_all(_seed_flow())
    session.add_all(_seed_contracts())
    session.commit()
    rep = build_flow_report(
        session, lookback_days=10, recent_days=5, end_date=_BASE + timedelta(days=9)
    )
    assert rep["found"] is True
    assert rep["as_of"] == "2026-06-10"
    roots = {r["root"] for r in rep["trend"]}
    assert {"AAA", "BBB", "CCC", "DDD"} <= roots
    assert "DDD" in rep["new"] and "CCC" in rep["fading"]
    # JSON-native: dates are ISO strings, no numpy/NaN leaked
    c0 = rep["contracts"][0]
    assert c0["root"] == "AAA"
    assert c0["expiry"] == "2026-09-18"
    assert c0["build_side"] == "accumulation"
    assert isinstance(c0["first_date"], str)
