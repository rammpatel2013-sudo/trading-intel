"""Unit tests for the pure flow aggregation + scorecard logic (no DB)."""

from __future__ import annotations

from datetime import date

import pandas as pd

from trading_intel.flow.aggregate import derive, rollup_by_contract, rollup_by_name
from trading_intel.flow.scorecard import score_names


def _prints() -> pd.DataFrame:
    # NVDA: net call buying (accumulation). AMD: net put/sell (distribution-ish).
    return pd.DataFrame(
        {
            "root": ["NVDA", "NVDA", "NVDA", "AMD", "AMD"],
            "expiry": [date(2026, 7, 17)] * 3 + [date(2026, 7, 17)] * 2,
            "strike": [210.0, 210.0, 220.0, 150.0, 150.0],
            "cp": ["C", "C", "C", "C", "C"],
            "side": ["buy", "buy", "sell", "sell", "sell"],
            "notional": [1_000_000, 2_000_000, 500_000, 800_000, 1_200_000],
            "size": [100, 200, 50, 80, 120],
            "price": [100.0, 100.0, 100.0, 100.0, 100.0],
            "delta": [0.5, 0.5, 0.5, 0.4, 0.4],
            "spot": [205.0, 205.0, 205.0, 140.0, 140.0],
        }
    )


def test_derive_adds_signed_delta_and_drops_null_root() -> None:
    df = _prints()
    df.loc[len(df)] = {**df.iloc[0].to_dict(), "root": None}
    out = derive(df)
    assert "signed_dollar_delta" in out.columns
    assert out["root"].notna().all()
    # buy rows positive signed delta, sell rows negative
    buy = out[out["side"] == "buy"]["signed_dollar_delta"]
    sell = out[out["side"] == "sell"]["signed_dollar_delta"]
    assert (buy > 0).all()
    assert (sell < 0).all()


def test_rollup_by_name_buy_sell_split() -> None:
    out = rollup_by_name(derive(_prints()))
    nvda = out[out["root"] == "NVDA"].iloc[0]
    assert nvda["prints"] == 3
    assert nvda["buy_notional"] == 3_000_000
    assert nvda["sell_notional"] == 500_000
    assert nvda["dominant_side"] == "buy"
    assert nvda["net_dollar_delta"] > 0  # net call buying


def test_rollup_by_contract_repeat_grain() -> None:
    out = rollup_by_contract(derive(_prints()))
    nvda210 = out[(out["root"] == "NVDA") & (out["strike"] == 210.0)].iloc[0]
    assert nvda210["n_prints"] == 2
    assert nvda210["buy_prints"] == 2
    assert nvda210["dominant_side"] == "buy"
    # spot + delta carried for moneyness/delta reads
    assert nvda210["spot"] == 205.0
    assert nvda210["avg_delta"] == 0.5


def test_scorecard_labels_accumulation_and_distribution() -> None:
    # Two days of daily-flow rows: GOOD persistently bought, BAD persistently sold.
    daily = pd.DataFrame(
        {
            "root": ["GOOD", "GOOD", "BAD", "BAD"],
            "trade_date": [date(2026, 6, 23), date(2026, 6, 24)] * 2,
            "total_notional": [5e6, 6e6, 4e6, 5e6],
            "buy_notional": [4.5e6, 5.5e6, 0.5e6, 0.6e6],
            "sell_notional": [0.5e6, 0.5e6, 3.5e6, 4.4e6],
            "net_dollar_delta": [3e6, 3.5e6, -2.5e6, -3e6],
            "gross_dollar_delta": [4e6, 4e6, 3e6, 3.2e6],
        }
    )
    board = score_names(daily, min_notional=0)
    good = board[board["root"] == "GOOD"].iloc[0]
    bad = board[board["root"] == "BAD"].iloc[0]
    assert good["label"] == "accumulation"
    assert good["accum_score"] > 0
    assert bad["label"] == "distribution"
    assert bad["accum_score"] < 0
    # ranked accumulation-first
    assert board.iloc[0]["root"] == "GOOD"


def test_scorecard_empty_input() -> None:
    assert score_names(pd.DataFrame()).empty


def test_scorecard_min_days_drops_one_day_flukes() -> None:
    # FLUKE seen 1 day with a huge score; STEADY seen 2 days.
    daily = pd.DataFrame(
        {
            "root": ["FLUKE", "STEADY", "STEADY"],
            "trade_date": [date(2026, 6, 24), date(2026, 6, 23), date(2026, 6, 24)],
            "total_notional": [9e6, 5e6, 6e6],
            "buy_notional": [9e6, 4.5e6, 5.5e6],
            "sell_notional": [0.0, 0.5e6, 0.5e6],
            "net_dollar_delta": [9e6, 3e6, 3.5e6],
            "gross_dollar_delta": [9e6, 4e6, 4e6],
        }
    )
    assert "FLUKE" in set(score_names(daily, min_days=1)["root"])
    board = score_names(daily, min_days=2)
    assert "FLUKE" not in set(board["root"])
    assert "STEADY" in set(board["root"])
