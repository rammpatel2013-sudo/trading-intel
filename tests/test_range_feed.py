"""Tests for scripts/range_feed.py (created 2026-11-13): the cockpit Range card feed (VIX + curve range model)."""
from __future__ import annotations

import datetime as dt
import json
import math
import os

import pandas as pd

from trading_intel.scheduler.jobs._script_report import load_script

rf = load_script("range_feed")


def test_forecast_reproduces_quantnug_11_12_card() -> None:
    # VIX 17.49 / VIX3M 19.24 at the 11/11 close, SPX closed 7,619.38: his card said 72 / 44 / 123 / 177
    f = rf.forecast(17.49, 19.24, 7619.38)
    assert (f["typical"], f["quiet"], f["big"], f["p98"]) == (73.6, 44.6, 125.5, 179.1)
    assert f["quiet"] < f["typical"] < f["big"] < f["p98"]
    assert rf.forecast(0, 19, 7600) is None and rf.forecast(17, 19, 0) is None


def test_inverted_curve_means_a_bigger_day_at_the_same_vix() -> None:
    steep = rf.forecast(22.0, 26.0, 7000)["typical"]
    inverted = rf.forecast(22.0, 20.0, 7000)["typical"]
    assert inverted > 1.25 * steep


def test_parse_cboe_files() -> None:
    csv = "DATE,OPEN,HIGH,LOW,CLOSE\n11/11/2026,17,18,16,17.49\n11/12/2026,18.36,19.34,17.38,18.70\nbad,row\n"
    assert rf.parse_csv(csv) == {dt.date(2026, 11, 11): 17.49, dt.date(2026, 11, 12): 18.70}
    js = {"data": [{"date": "2026-11-12", "open": "7550.84", "high": "7652.76", "low": "7550.84", "close": "7592.4"},
                   {"date": "2026-11-13", "open": "1", "high": "5", "low": "5", "close": "5"}]}
    assert rf.parse_ohlc(js) == {dt.date(2026, 11, 12): (7550.84, 7652.76, 7550.84, 7592.4)}


def _path(day_range: float = 40.0, stale: float | None = None) -> dict:
    """Synthetic minute path 09:30-16:00: rises to +day_range by 12:00 then flat."""
    px = {}
    for m in range(570, 961):
        k = min(m - 571, 150) / 150.0
        px[m] = 7000.0 + day_range * max(k, 0.0)
    if stale is not None:
        px[570] = stale            # the minute log's 09:30 row carries the prior close
    return px


def test_shares_skip_the_stale_0930_row_and_reach_one() -> None:
    sh = rf.shares_from_prices(_path(40.0, stale=6900.0), 960)
    assert len(sh) == len(rf.MARKS) and sh[-1] == 1.0
    assert sh[rf.MARKS.index(155)] == 1.0 and sh[rf.MARKS.index(145)] < 1.0   # all made by 12:01
    assert 0.15 < sh[rf.MARKS.index(30)] < 0.25                # ~29/150 of it by 10:00
    assert all(a <= b for a, b in zip(sh, sh[1:]))


def test_shares_need_a_full_normal_session() -> None:
    assert rf.shares_from_prices(_path(), 13 * 60) is None                       # early close
    short = {m: v for m, v in _path().items() if m < 700}
    assert rf.shares_from_prices(short, 960) is None
    flat = {m: 7000.0 for m in range(570, 961)}
    assert rf.shares_from_prices(flat, 960) is None


def test_shares_from_bars_use_highs_and_lows() -> None:
    bars = [(m, 7000.0 + (5.0 if m == 600 else 0.0), 7000.0 - (5.0 if m == 900 else 0.0)) for m in range(570, 960)]
    bars += [(560, 9999.0, 1.0)]                                # pre-market bar ignored
    sh = rf.shares_from_bars(bars, 960)
    assert sh[rf.MARKS.index(30)] == 0.0 and sh[rf.MARKS.index(35)] == 0.5 and sh[-1] == 1.0


def test_guess_blends_logs_and_never_drops_below_so_far() -> None:
    assert math.isclose(rf.guess(100.0, 0.5, 0.0, 40.0), 100.0)
    assert math.isclose(rf.guess(100.0, 0.5, 1.0, 40.0), 80.0)
    assert math.isclose(rf.guess(100.0, 0.5, 0.5, 40.0), math.sqrt(80.0 * 100.0))
    assert rf.guess(50.0, 0.5, 0.0, 70.0) == 70.0


def _days_and_fc(n: int) -> tuple:
    days, fc = {}, {}
    d = dt.date(2025, 1, 2)
    for i in range(n):
        R = 60.0 + (i % 7) * 10.0
        T = 70.0
        sh = [min(1.0, (mk / 390.0) ** 0.6 * (1.0 + 0.05 * ((i % 3) - 1))) for mk in rf.MARKS]
        sh[-1] = 1.0
        days[d.isoformat()] = {"src": "spy_1m", "sh": [round(x, 4) for x in sh]}
        fc[d.isoformat()] = ({"typical": T, "quiet": T * 0.6, "big": T * 1.7, "p98": T * 2.4}, R)
        d += dt.timedelta(days=1)
    return days, fc


def test_pace_is_rough_until_sixty_sessions() -> None:
    days, fc = _days_and_fc(10)
    p = rf.build_pace(days, fc)
    assert p["rough"] and p["n"] == 10 and p["w"] == p["curve"]
    days, fc = _days_and_fc(80)
    p = rf.build_pace(days, fc)
    assert not p["rough"] and p["n_fit"] == 80
    assert all(0.0 <= w <= 1.0 for w in p["w"]) and all(lo <= hi for lo, hi in zip(p["lo"], p["hi"]))
    assert p["w"][-1] > 0.9                                     # at the close the range so far IS the answer
    assert rf.build_pace({}, {})["rough"]


def test_band_stats_counts() -> None:
    f = {"typical": 100.0, "quiet": 60.0, "big": 170.0, "p98": 240.0}
    s = rf.band_stats([(f, 50.0), (f, 100.0), (f, 200.0), (f, 250.0)])
    assert s == {"n": 4, "under_quiet": 1, "over_big": 2, "over_1in50": 1, "over_2x": 1, "in_band": 1, "in_band_pct": 25.0}


def _fetch_stub():
    days = [dt.date(2026, 11, 2) + dt.timedelta(days=i) for i in range(12)]
    days = [d for d in days if rf.market_cal.is_session(d)]
    vix = {d: 18.0 + i * 0.1 for i, d in enumerate(days)}
    v3 = {d: 20.0 for d in days}
    ohlc = {d: (7000.0, 7040.0 + i, 6990.0, 7010.0) for i, d in enumerate(days)}
    return lambda: (vix, v3, ohlc)


def test_run_writes_card_and_store_and_is_repeatable(tmp_path) -> None:
    data = str(tmp_path)
    with open(os.path.join(data, "flow_20261111.ndjson"), "w", encoding="utf-8") as fh:
        for m, x in _path(50.0, stale=6950.0).items():
            fh.write(json.dumps({"t": "%02d:%02d" % divmod(m, 60), "s": "SPX", "x": x}) + "\n")
            fh.write(json.dumps({"t": "%02d:%02d" % divmod(m, 60), "s": "SPY", "x": x / 10}) + "\n")
    card = rf.run(data, dt.date(2026, 11, 13), False, False, fetch=_fetch_stub(), now_txt="t")
    assert card["for_date"] == "2026-11-16" and card["inputs_date"] == "2026-11-13"     # Fri inputs -> Mon
    assert card["quiet"] < card["typical"] < card["big"] < card["p98"]
    assert card["pace"]["n"] == 1 and card["pace"]["rough"] and card["feed"]["logs_added"] == ["2026-11-11"]
    assert len(card["recent"]) == 6 and card["scores"]["oos"]["n"] > 0
    store = json.load(open(os.path.join(data, "range_pace.json")))
    assert store["days"]["2026-11-11"]["src"] == "spx_log"
    card2 = rf.run(data, dt.date(2026, 11, 13), False, False, fetch=_fetch_stub(), now_txt="t")
    assert card2["feed"]["logs_added"] == [] and {k: v for k, v in card2.items() if k != "feed"} == \
        {k: v for k, v in card.items() if k != "feed"}


def test_spy_pull_from_a_fake_client_keeps_our_log_days() -> None:
    from zoneinfo import ZoneInfo

    class Fake:
        def aggs(self, ticker, *, frm, to, multiplier, timespan, limit):  # noqa: ANN001, ANN201
            assert ticker == "SPY" and timespan == "minute"
            rows = []
            d = dt.date.fromisoformat(frm)
            while d <= dt.date.fromisoformat(to):
                if rf.market_cal.is_session(d) and rf.market_cal.session_close(d) == (16, 0):
                    for m in range(560, 970):
                        ts = dt.datetime(d.year, d.month, d.day, m // 60, m % 60, tzinfo=ZoneInfo("America/New_York"))
                        rows.append({"t": int(ts.timestamp() * 1000), "h": 700.0 + (m - 560) * 0.01, "l": 699.0})
                d += dt.timedelta(days=1)
            return pd.DataFrame(rows)

    store = {"v": rf.V, "days": {"2026-11-11": {"src": "spx_log", "sh": [1.0] * len(rf.MARKS)}}}
    info = rf.pull_spy_pace(store, years=0.05, client=Fake(), sleep=0)
    assert info["added"] > 5 and info["n_errors"] == 0
    assert store["days"]["2026-11-11"]["src"] == "spx_log"
    some = next(v for k, v in store["days"].items() if v["src"] == "spy_1m")
    assert some["sh"][-1] == 1.0 and some["sh"][0] < some["sh"][-1]


def test_make_card_joins_the_pace_store_to_cboe_days() -> None:
    # the store is keyed by ISO strings, the Cboe maps by date objects: the join must still find them
    days = [dt.date(2025, 1, 2) + dt.timedelta(days=i) for i in range(140)]
    days = [d for d in days if rf.market_cal.is_session(d)]
    vix = {d: 15.0 + (i % 9) for i, d in enumerate(days)}
    v3 = {d: 18.0 for d in days}
    ohlc = {d: (6000.0, 6040.0 + 7 * (i % 11), 6000.0, 6010.0) for i, d in enumerate(days)}
    store = {d.isoformat(): {"src": "spy_1m", "sh": [round(min(1.0, (mk / 390.0) ** (0.5 + 0.02 * (i % 5))), 4) for mk in rf.MARKS]}
             for i, d in enumerate(days)}
    card = rf.make_card(vix, v3, ohlc, store, days[-1], "t")
    assert card["pace"]["n_fit"] >= 60 and not card["pace"]["rough"]
    assert card["scores"]["pace_stored"]["10:00"]["n"] == card["pace"]["n_fit"]
