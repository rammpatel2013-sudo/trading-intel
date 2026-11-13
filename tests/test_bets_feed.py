"""Pure-logic tests for scripts/bets_feed.py (created 2026-11-12): the cockpit Bets tab feed."""
from __future__ import annotations

import json
from datetime import date, datetime

from trading_intel.scheduler.jobs._script_report import load_script

bf = load_script("bets_feed")


def _p(root, cp, side, notional, *, strike=100.0, expiry="2027-03-19", size=100, delta=0.4, iv=0.5,
       fin=False, group=None, t="10:00", spot=None, price=None):
    return {"ts": datetime.fromisoformat(f"2026-11-12T{t}:00"), "root": root,
            "expiry": date.fromisoformat(expiry), "strike": strike, "cp": cp, "side": side,
            "price": price, "size": size, "notional": notional, "spot": spot,
            "delta": None if delta is None else (delta if cp == "C" else -abs(delta)), "iv": iv,
            "is_financing": fin, "leg_group": group}


def test_leg_direction_calls_bought_puts_sold_are_bullish() -> None:
    assert bf.leg_dir("buy", "C") == 1 and bf.leg_dir("sell", "P") == 1
    assert bf.leg_dir("buy", "P") == -1 and bf.leg_dir("sell", "C") == -1
    assert bf.leg_dir("mid", "C") == 0 and bf.leg_dir(None, "P") == 0 and bf.leg_dir("buy", None) == 0


def test_credit_put_spread_counts_bullish_by_its_legs() -> None:
    # sell the 55 put, buy the 50 put: a net CREDIT (negative package price) but a bullish bet
    rows = [_p("ABC", "P", "sell", 21_000, strike=55, size=100, group="g1"),
            _p("ABC", "P", "buy", 20_000, strike=50, size=100, group="g1")]
    per, _out = bf.classify(rows)
    assert per["ABC"]["bull"] == 21_000 and per["ABC"]["bear"] == 20_000 and per["ABC"]["net"] == 1_000
    assert per["ABC"]["big"]["net"] == 1_000
    assert per["ABC"]["big"]["txt"] == "19Mar27 50P buy \u00b7 55P sell \u00d7100"
    cal = [_p("ABC", "C", "sell", 9_000, strike=100, expiry="2026-12-18", size=50),
           _p("ABC", "C", "buy", 15_000, strike=100, size=50)]          # a calendar: each leg its own expiry
    assert bf.describe(cal) == "18Dec26 100C sell \u00b7 19Mar27 100C buy \u00d750"


def test_financing_unsigned_and_index_prints_are_left_out() -> None:
    rows = [_p("MU", "P", "buy", 71_000_000, strike=300, spot=230, price=71.5, delta=0.81, iv=2.7),  # ITM
            _p("LULU", "P", "sell", 62_000_000, delta=0.9999),             # deep ITM by delta
            _p("BA", "P", "sell", 42_000_000, fin=True),                   # tagged financing by tas_capture
            # the 11/12 DELL print: Jan28 550 put, no greeks, $223 of which $203 intrinsic
            _p("DELL", "P", "sell", 22_300_000, strike=550, expiry="2028-01-21", spot=347, price=223,
               delta=None, iv=None, size=1000),
            _p("ABCL", "C", "buy", 2_000_000, strike=6, spot=4.1, price=0.35, delta=0.3, iv=2.2),  # OTM, high IV: a bet
            _p("NVDA", "C", "mid", 5_000_000),                             # unsigned
            _p("SPY", "C", "buy", 9_000_000),                              # not a stock
            _p("NVDA", "C", "buy", 59_000_000)]
    per, out = bf.classify(rows)
    assert set(per) == {"NVDA", "ABCL"} and per["NVDA"]["bull"] == 59_000_000
    assert out["fin_n"] == 4 and out["fin_prem"] == 197_300_000
    assert out["fin_by"] == {"tagged": 1, "delta": 1, "itm": 2, "iv": 0}
    assert out["uns_n"] == 1 and out["nonstock_n"] == 1
    # IV >= 150% only counts as financing on an in-the-money print with some time value left
    assert bf.fin_reason(_p("X", "C", "buy", 1, strike=10, spot=12, price=3.0, delta=0.6, iv=1.9)) == "iv"
    assert bf.fin_reason(_p("X", "C", "buy", 1, strike=10, spot=8, price=0.9, delta=0.3, iv=1.9)) is None


def test_tape_times_on_utc_become_new_york_times() -> None:
    utc = [_p("NVDA", "C", "buy", 9_000_000, t="15:42"), _p("NVDA", "C", "buy", 1_000_000, t="20:59")]
    bf.to_et(utc)
    assert [r["ts"].strftime("%H:%M") for r in utc] == ["10:42", "15:59"]      # EST: UTC-5
    et = [_p("AAPL", "P", "buy", 9_000_000, t="09:41"), _p("AAPL", "P", "buy", 1_000_000, t="15:59")]
    bf.to_et(et)
    assert [r["ts"].strftime("%H:%M") for r in et] == ["09:41", "15:59"]      # already New York time
    pay = bf.build(utc, {"NVDA": 4e12}, "2026-11-12", datetime(2026, 11, 12, 16, 0))
    assert pay["asof"] == "15:59" and pay["mega"]["top"][0]["big"]["t"] == "10:42"
    assert pay["totals"]["fin_by"] == {"tagged": 0, "delta": 0, "itm": 0, "iv": 0}


def test_biggest_bet_is_the_largest_package_and_mixed_size_clusters_split() -> None:
    rows = [_p("NVDA", "C", "buy", 59_000_000, strike=170, size=35_000, group="a"),
            _p("NVDA", "C", "sell", 40_000_000, strike=185, size=35_000, group="a"),     # net +19M
            _p("NVDA", "P", "sell", 28_000_000, strike=140, size=25_000),                 # +28M single
            _p("NVDA", "C", "buy", 3_000_000, strike=200, size=900, group="b"),
            _p("NVDA", "P", "buy", 2_000_000, strike=150, size=400, group="b")]           # sizes differ: not a package
    per, _ = bf.classify(rows)
    assert per["NVDA"]["big"]["net"] == 28_000_000
    assert per["NVDA"]["big"]["txt"] == "19Mar27 140P sell \u00d725K"
    assert per["NVDA"]["net"] == (59 + 28 + 3 - 40 - 2) * 1_000_000


def test_buckets_and_top_five_by_net_conviction() -> None:
    caps = {f"MEGA{i}": 300e9 + i for i in range(7)}
    caps.update({"SML": 1.5e9, "MIC": 150e6, "MID": 5e9})
    rows = [_p(f"MEGA{i}", "C", "buy", (i + 1) * 1_000_000) for i in range(7)]
    rows += [_p("SML", "P", "buy", 4_000_000), _p("MIC", "C", "buy", 900_000),
             _p("MID", "C", "buy", 7_000_000), _p("NOCAP", "C", "buy", 1_000_000)]
    pay = bf.build(rows, caps, "2026-11-12", datetime(2026, 11, 12, 15, 30))
    assert [r["s"] for r in pay["mega"]["top"]] == ["MEGA6", "MEGA5", "MEGA4", "MEGA3", "MEGA2"]
    assert pay["mega"]["names"] == 7 and pay["mega"]["bull"] == 28_000_000
    assert [r["s"] for r in pay["smallmicro"]["top"]] == ["SML", "MIC"]
    assert pay["smallmicro"]["top"][0]["net"] == -4_000_000
    assert pay["mid"]["top"][0]["s"] == "MID" and pay["totals"]["nocap"] == 1
    assert pay["asof"] == "10:00" and bf.bucket_of(250e6) == "micro" and bf.bucket_of(None) is None


class _FakeFmp:
    def __init__(self, screener=None, quotes=None, profiles=None):
        self.screener, self.quotes, self.profiles, self.calls = screener, quotes or {}, profiles or {}, []

    def fmp(self, endpoint, params=None):
        self.calls.append(endpoint)
        if endpoint == "company-screener":
            return self.screener
        if endpoint == "batch-quote":
            syms = params["symbols"].split(",")
            return [{"symbol": s, "marketCap": self.quotes[s]} for s in syms if s in self.quotes]
        if endpoint == "profile":
            return [self.profiles.get(params["symbol"], {})]
        return None


def test_caps_from_the_screener_then_batch_quote_and_misses_retry_next_day() -> None:
    screener = [{"symbol": f"S{i}", "marketCap": 1e9 + i} for i in range(1200)]
    c = _FakeFmp(screener=screener, quotes={"NEW": 3e9})
    cache: dict = {}
    got = bf.fill_caps(c, ["S1", "NEW", "SPY", "ZZZ"], cache, "2026-11-12")
    assert got == {"S1": 1e9 + 1} and cache["screener_ok"]
    assert c.calls == ["company-screener"]                     # NEW / ZZZ are not stocks per the screener
    c2 = _FakeFmp(screener=None, quotes={"NEW": 3e9})          # screener refused: batch-quote, misses kept a day
    cache2: dict = {}
    got2 = bf.fill_caps(c2, ["NEW", "ZZZ", "SPY"], cache2, "2026-11-12")
    assert got2 == {"NEW": 3e9} and cache2["miss"] == {"ZZZ": "2026-11-12"}
    assert bf.fill_caps(c2, ["NEW", "ZZZ"], cache2, "2026-11-12") == {"NEW": 3e9}
    assert c2.calls.count("batch-quote") == 1                  # ZZZ not retried the same day
    assert bf.etf_flag(_FakeFmp(profiles={"SOXX2": {"isEtf": True}}), "SOXX2") is True


def test_run_once_writes_live_and_day_files(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(bf, "DATA", str(tmp_path / "data"))
    monkeypatch.setattr(bf, "CACHE", str(tmp_path / "reports" / "bets" / "mktcap_cache.json"))
    rows = [_p("BIG", "C", "buy", 9_000_000), _p("TINY", "P", "buy", 800_000),
            _p("ETFX", "C", "buy", 5_000_000)]
    monkeypatch.setattr(bf, "load_rows", lambda s, d: rows)

    class _S:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    client = _FakeFmp(screener=None, quotes={"BIG": 500e9, "TINY": 100e6, "ETFX": 250e9},
                      profiles={"ETFX": {"isEtf": True}})
    pay = bf.run_once(lambda: _S(), client, date(2026, 11, 12))
    assert [r["s"] for r in pay["mega"]["top"]] == ["BIG"]          # the ETF was checked and dropped
    assert [r["s"] for r in pay["smallmicro"]["top"]] == ["TINY"]
    live = json.loads((tmp_path / "data" / "bets_live.json").read_text())
    day = json.loads((tmp_path / "data" / "bets_20261112.json").read_text())
    assert live["mega"]["top"][0]["s"] == "BIG" and len(day["snaps"]) == 1
    bf.run_once(lambda: _S(), client, date(2026, 11, 12))           # same as-of minute: replaced, not added
    day = json.loads((tmp_path / "data" / "bets_20261112.json").read_text())
    assert len(day["snaps"]) == 1
    day["snaps"] = [{"t": "09:55"}, {"t": "20:59"}]                  # an earlier run wrote a later as-of
    (tmp_path / "data" / "bets_20261112.json").write_text(json.dumps(day))
    bf.run_once(lambda: _S(), client, date(2026, 11, 12))
    day = json.loads((tmp_path / "data" / "bets_20261112.json").read_text())
    assert [x["t"] for x in day["snaps"]] == ["09:55", "10:00"]      # kept in time order
    cache = json.loads((tmp_path / "reports" / "bets" / "mktcap_cache.json").read_text())
    assert cache["etf"]["ETFX"] is True and cache["caps"]["BIG"] == 500e9
