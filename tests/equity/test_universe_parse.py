"""E1 foundation: membership walk-back, parsers, market cap, fundamentals panels."""

from datetime import date

import numpy as np

from trading_intel.equity import parse, store, universe
from trading_intel.equity.panel import build_panel, fundamental_points


def test_sp500_walk_back_and_rename_skip():
    current = [{"symbol": "AAA"}, {"symbol": "BRK.B"}, {"symbol": "DD"}]
    changes = [
        {
            "date": "2025-06-01",
            "addedTicker": "AAA",
            "removedTicker": "OLD",
            "addedSecurity": "A",
            "removedSecurity": "O",
        },
        {
            "date": "2024-03-01",
            "addedTicker": "OLD",
            "removedTicker": "",
            "addedSecurity": "O",
            "removedSecurity": "",
        },
        {
            "date": "2024-01-02",
            "addedTicker": "DD",
            "removedTicker": "DWDP",
            "addedSecurity": "DuPont",
            "removedSecurity": "DuPont",
        },
    ]
    ivs = universe.sp500_intervals(current, changes, since=date(2023, 1, 1))
    by = {(iv.symbol, iv.start_date, iv.end_date) for iv in ivs}
    assert ("AAA", date(2025, 6, 1), None) in by
    assert ("OLD", date(2024, 3, 1), date(2025, 6, 1)) in by
    assert ("BRK-B", None, None) in by  # dot -> dash
    assert ("DD", None, None) in by and not any(iv.symbol == "DWDP" for iv in ivs)  # rename skipped
    assert universe.members_on(ivs, date(2025, 1, 2)) == {"OLD", "BRK-B", "DD"}
    assert universe.members_on(ivs, date(2025, 7, 1)) == {"AAA", "BRK-B", "DD"}
    assert "OLD" in universe.all_symbols(ivs, since=date(2024, 1, 1))


def test_etf_intervals_filter_cash_rows():
    ivs = universe.etf_intervals(
        [{"asset": "ATI"}, {"asset": ""}, {"asset": "MOG-A"}, {"asset": "USD123"}], "SP400", "IJH"
    )
    assert sorted(iv.symbol for iv in ivs) == ["ATI", "MOG-A"] and ivs[0].source == "ijh_today"


def test_bar_rows_sorted_and_cleaned():
    rows = parse.bar_rows(
        "X",
        [
            {"date": "2026-11-13", "close": 10, "open": 9, "high": 11, "low": 8, "volume": 5},
            {"date": "2026-11-12", "close": 0},
            {"date": "bad", "close": 3},
            {"date": "2026-11-11", "close": 9.5},
        ],
    )
    assert [r["date"] for r in rows] == [date(2026, 11, 11), date(2026, 11, 13)] and rows[1][
        "volume"
    ] == 5


def test_fundamental_rows_point_in_time_and_mcap():
    stmts = {
        "income": [
            {
                "date": "2026-06-30",
                "acceptedDate": "2026-08-01 08:00:00",
                "revenue": 100,
                "weightedAverageShsOutDil": 10,
            }
        ],
        "balance": [
            {"date": "2026-06-30", "filingDate": "2026-07-30", "totalStockholdersEquity": 50}
        ],
        "cash": [{"date": "2026-03-31", "operatingCashFlow": 7}],
    }
    rows = parse.fundamental_rows("X", stmts)
    q2 = next(r for r in rows if r["period_end"] == date(2026, 6, 30))
    assert q2["available_date"] == date(2026, 8, 1) and q2["total_equity"] == 50
    q1 = next(r for r in rows if r["period_end"] == date(2026, 3, 31))
    assert q1["available_date"] == date(2026, 5, 15)  # period end + 45 days when no filing date
    bars = [
        {"symbol": "X", "date": date(2026, 7, 15), "close": 2.0},
        {"symbol": "X", "date": date(2026, 8, 3), "close": 3.0},
    ]
    funds = [
        {"available_date": date(2026, 8, 1), "shares_diluted": 10},
        {"available_date": date(2026, 5, 1), "shares_diluted": 8},
    ]
    mc = store.attach_market_cap(bars, funds)
    assert mc[0]["market_cap"] == 16.0 and mc[1]["market_cap"] == 30.0


def test_ttm_and_growth_need_contiguous_quarters():
    qs = []
    for k, (y, m) in enumerate(
        [(2024, 3), (2024, 6), (2024, 9), (2024, 12), (2025, 3), (2025, 6), (2025, 9), (2025, 12)]
    ):
        end = date(y, m, 28)
        qs.append(
            {
                "period_end": end,
                "available_date": date(y + (m == 12), (m % 12) + 1, 28),
                "revenue": 100 + 10 * k,
                "gross_profit": 40,
                "net_income": 10,
                "operating_cash_flow": 12,
                "total_equity": 500,
                "total_assets": 1000,
                "total_debt": 200,
            }
        )
    pts = fundamental_points(qs)
    last = pts[-1][1]
    assert last["ttm_revenue"] == sum(100 + 10 * k for k in range(4, 8))
    assert (
        abs(
            last["rev_growth"]
            - (sum(100 + 10 * k for k in range(4, 8)) / sum(100 + 10 * k for k in range(4)) - 1)
        )
        < 1e-12
    )
    assert pts[2][1]["ttm_revenue"] is None  # fewer than 4 quarters


def test_build_panel_membership_mask():
    ivs = [
        universe.Interval("A", "SP500", date(2026, 1, 3), None, "x"),
        universe.Interval("B", "SP500", None, date(2026, 1, 3), "x"),
    ]
    bars = [
        (s, date(2026, 1, d), 1, 1, 1, 1.0, 1, 10.0) for s in ("A", "B", "SPY") for d in (2, 3, 4)
    ]
    p = build_panel(bars, ivs, {}, extra_symbols=["SPY"])
    assert p.symbols == ["A", "B", "SPY"]
    assert p.member[:, 0].tolist() == [False, True, True] and p.member[:, 1].tolist() == [
        True,
        False,
        False,
    ]
    assert not p.member[:, 2].any() and np.isnan(p.returns[0]).all()


def test_upsert_mixed_keys_sqlite():
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from trading_intel.memory.models_equity import EquityFundamental

    eng = create_engine("sqlite://")
    EquityFundamental.__table__.create(eng)
    with Session(eng) as s:
        rows = parse.fundamental_rows("X", {"income": [{"date": "2026-06-30", "revenue": 5}],
                                            "cash": [{"date": "2026-03-31", "stockBasedCompensation": 2}]})
        assert all(len(r) == len(rows[0]) for r in rows)
        store.upsert(s, EquityFundamental, [{"symbol": "Y", "period_end": date(2026, 1, 1), "revenue": 1},
                                             {"symbol": "Y", "period_end": date(2026, 4, 1), "sbc": 3}],
                     ["symbol", "period_end"])
        got = {r.period_end: (r.revenue, r.sbc) for r in s.execute(select(EquityFundamental)).scalars()}
        assert got == {date(2026, 1, 1): (1, None), date(2026, 4, 1): (None, 3)}
