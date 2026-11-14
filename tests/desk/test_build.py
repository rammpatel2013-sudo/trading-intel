"""Screen filters fitted to his 11/13 list (2026-11-14): BSR >= 1, price >= $20, day $ volume >= $30M."""

from datetime import date

from trading_intel.desk import build, metrics


def _row(sym, **kw):
    r = {
        "symbol": sym,
        "name": sym,
        "industry": "Packaged Foods",
        "group": "Consumer Staples",
        "is_etf": False,
        "close": 50.0,
        "high": 51.0,
        "low": 49.0,
        "volume": 2_000_000,
        "d1": 1.5,
        "d5": -2.0,
        "bsr": 2.0,
        "dvol": 100e6,
        "dvol20": 90e6,
        "dark": metrics.dark_windows([]),
        "opt_volume": 1000,
        "bull": None,
        "st_gex": None,
        "total_gex": None,
        "lev": None,
        "gex_vol": None,
    }
    r.update(kw)
    return r


def test_screen_filters():
    rows = {
        s: r
        for s, r in [
            ("OK", _row("OK")),
            ("LOWBSR", _row("LOWBSR", bsr=0.6)),  # closed in the lower half of the range
            ("CHEAP", _row("CHEAP", close=12.0)),  # under $20
            ("THIN", _row("THIN", dvol=12e6)),  # under $30M today
            ("UP5D", _row("UP5D", d5=1.0)),  # not a down week
        ]
    }
    p = build.build(
        {
            "day": date(2026, 11, 13),
            "rows": rows,
            "coverage": {"finra": False, "options": False, "gamma": False, "n_universe": 5},
        }
    )
    assert [r["symbol"] for r in p["screen"]] == ["OK"]
    assert p["top_pick"] is None and p["two_leg"] == []
