"""Confluence merge, rate of change, scoring and the JSON round-trip."""

from __future__ import annotations

from datetime import date, timedelta

from trading_intel.market import confluence as cf
from trading_intel.market import confluence_score as cs
from trading_intel.market.confluence_build import (
    ConfluenceInputs,
    compute_readout,
    inputs_from_json,
    inputs_to_json,
)
from trading_intel.market.confluence_layers import Bar, ChainRow, SmilePoint, VixOption


def L(layer: str, level: float, s: float = 1.0) -> cf.Level:  # noqa: N802
    return cf.Level(layer, level, f"{layer} {level}", s)


def test_cluster_merges_different_layers_within_window() -> None:
    zones = cf.merge([L("greeks", 6990), L("oi", 7000), L("price", 7002), L("crowd", 7010),
                      L("oi", 7150), L("vix", 7216)], spot=7376.86, window=25)
    key = [z for z in zones if z.key]
    assert len(key) == 1
    z = key[0]
    assert z.layers == ["greeks", "oi", "price", "crowd"] and z.n_layers == 4
    assert (z.low, z.high) == (6990, 7010) and z.centre == 7000.5
    assert z.distance == round(7000.5 - 7376.86, 1)


def test_zone_span_never_exceeds_window() -> None:
    lv = [L("oi", 7000 + 10 * i) for i in range(20)] + [L("price", 7095), L("vix", 7100)]
    for z in cf.merge(lv, window=25):
        assert z.high - z.low <= 25
    best = max(cf.merge(lv, window=25), key=lambda z: z.n_layers)
    assert best.n_layers == 3 and best.low <= 7095 <= best.high


def test_centre_is_strength_weighted() -> None:
    z = cf.make_zone([L("oi", 7000, 3.0), L("price", 7020, 1.0)])
    assert z.centre == 7005.0


def test_same_layer_does_not_count_twice() -> None:
    z = cf.make_zone([L("oi", 7000), L("oi", 7005), L("price", 7010)])
    assert z.n_layers == 2 and not z.key


def test_oi_change_summed_in_zone() -> None:
    z = cf.make_zone([cf.Level("oi", 7000, "a", 1, "put_oi", {"oi_change": 3000}),
                      cf.Level("oi", 7010, "b", 1, "call_oi", {"oi_change": -500}),
                      cf.Level("price", 7005, "c")])
    assert z.oi_change == 2500


def test_rate_of_change_vs_yesterday() -> None:
    zones = cf.merge([L("greeks", 7000), L("oi", 7010), L("price", 7020)], spot=7376.86)
    zones[0].oi_change = 4000.0
    prior = [{"centre": 6980.0, "n_layers": 2, "oi_change": 1000.0}]
    cf.rate_of_change(zones, prior, spot=7376.86, prior_spot=7353.73)
    rc = zones[0].roc
    assert rc["matched"] and rc["centre_shift"] == round(zones[0].centre - 6980, 1)
    assert rc["layer_change"] == 1 and rc["oi_change_delta"] == 3000.0
    assert rc["distance_prior"] == round(6980 - 7353.73, 1)
    far = cf.merge([L("oi", 8000)], spot=7376.86)
    cf.rate_of_change(far, prior, spot=7376.86, prior_spot=7353.73)
    assert far[0].roc == {"matched": False}


def test_build_readout_reports_empty_layers_and_disclaimer() -> None:
    r = cf.build_readout({"oi": [{"level": 7000, "label": "x", "strength": 1}]}, spot=7300,
                         as_of="2026-11-06")
    assert r["disclaimer"] == "Positioning read, not a trade signal."
    assert any("Greek peaks" in g for g in r["gaps"])
    assert r["zones"][0]["n_layers"] == 1 and r["key_zones"] == []


def test_score_zone_touched_held_broke() -> None:
    z = {"centre": 7000, "low": 6990, "high": 7010}
    d = date(2026, 11, 9)
    held = cs.score_zone(z, 7100, [Bar(d, 7050, 7060, 6995, 7040)])
    broke = cs.score_zone(z, 7100, [Bar(d, 7050, 7060, 6950, 6960)])
    miss = cs.score_zone(z, 7100, [Bar(d, 7090, 7120, 7050, 7100)])
    assert held == {"touched": True, "held": True, "broke": False}
    assert broke == {"touched": True, "held": False, "broke": True}
    assert miss == {"touched": False, "held": False, "broke": False}
    assert cs.score_zone(z, 7000, [Bar(d, 1, 1, 1, 1)]) is None  # spot inside the zone
    up = cs.score_zone({"centre": 7200, "low": 7195, "high": 7205}, 7100,
                       [Bar(d, 7150, 7210, 7140, 7180)])
    assert up["held"]  # zone above spot, closed back below it


def test_score_days_groups_and_small_sample() -> None:
    d0 = date(2026, 11, 2)
    days = [{"as_of": d0.isoformat(), "spot": 7100, "zones": [
        {"centre": 7000, "low": 6995, "high": 7005, "n_layers": 3},
        {"centre": 7200, "low": 7200, "high": 7200, "n_layers": 1},
        {"centre": 7050, "low": 7045, "high": 7055, "n_layers": 2}]}]
    bars = [Bar(d0 + timedelta(days=i), 7080, 7120, 6990, 7060) for i in range(1, 7)]
    res = cs.score_days(days, bars)
    k1 = res["horizons"]["1"]["key"]
    assert k1["n"] == 1 and k1["touched"] == 1 and k1["held"] == 1 and k1["small_sample"]
    assert res["horizons"]["5"]["single"]["n"] == 1
    assert res["horizons"]["1"]["multi2"]["n"] == 1
    assert "too few" in cs.headline(res)
    assert cs.score_days(days, bars[:2])["days_scored"]["5"] == 0


def _inputs() -> ConfluenceInputs:
    d = date(2026, 11, 6)
    chain = [ChainRow(7000.0, "P", 14, 0.25, 120_000, 3000), ChainRow(7000.0, "C", 14, 0.25, 100_000, 0),
             ChainRow(7200.0, "P", 5, 0.26, 10_000, 100), ChainRow(7650.0, "C", 5, 0.19, 12_000, -4000)]
    bars = [Bar(d - timedelta(days=i), 7400, 7450, 7350, 7400 + i) for i in range(300)][::-1]
    smile = [SmilePoint(r, dl, s, 0.25 if s == "put" else 0.18, 7378.9)
             for r in (7, 14) for dl in (20.0, 25.0, 30.0) for s in ("put", "call")]
    return ConfluenceInputs(as_of=d, spot=7376.86, chain=chain, vix_spot=25.64,
                            vix_options=[VixOption(date(2026, 11, 18), 30, "call", 338_000)],
                            spx_bars=bars, smile=smile)


def test_compute_readout_end_to_end_and_json_round_trip() -> None:
    inp = _inputs()
    r = compute_readout(inp)
    assert r["as_of"] == "2026-11-06" and r["spot"] == 7376.86
    assert set(r["layers"]) == set(cf.LAYERS) and r["layers"]["oi"]
    assert any(x["kind"] == "vix_call_oi" for x in r["layers"]["vix"])
    back = inputs_from_json(inputs_to_json(inp))
    assert back == inp
    assert compute_readout(back)["zones"] == r["zones"]


def test_info_levels_do_not_count_toward_layers_or_key() -> None:
    info = cf.Level("greeks", 7005, "vol-shock peak (info)", 0.0, "vol_shock_peak", info=True)
    base = [L("oi", 7000), L("price", 7010)]
    zones = cf.merge([*base, info], spot=7300)
    z = next(z for z in zones if z.low <= 7005 <= z.high)
    assert z.layers == ["oi", "price"] and z.n_layers == 2 and not z.key
    assert z.info == [info] and all(x is not info for x in z.levels)
    assert len(zones) == len(cf.merge(base, spot=7300))  # no extra zone from the info level
    # an info level alone never makes a zone
    assert cf.merge([info], spot=7300) == []
    r = cf.build_readout(
        {"greeks": [{"level": 7005, "label": "vol-shock peak (info)", "strength": 0,
                     "kind": "vol_shock_peak", "info": True}],
         "oi": [{"level": 7000, "label": "a"}], "price": [{"level": 7010, "label": "b"}]},
        spot=7300, as_of="2026-11-06")
    assert r["key_zones"] == [] and r["zones"][0]["n_layers"] == 2
    assert r["zones"][0]["info_levels"][0]["level"] == 7005
    assert any("Greek peaks" in g for g in r["gaps"])  # info-only layer counts as empty
    assert "not on anyone's position" in r["info_note"]


def test_cockpit_payload_shape() -> None:
    from trading_intel.market.confluence_build import cockpit_payload

    r = compute_readout(_inputs())
    prior = dict(r, as_of="2026-11-05", spot=7353.73)
    d = cockpit_payload(r, prior=prior, score={"horizons": {"5": {
        "key": {"n": 3, "hold_rate": 0.5}, "single": {"n": 9, "hold_rate": 0.4},
        "multi2": {"n": 1, "hold_rate": None}}}, "n_days_stored": 4, "min_sample": 30},
        bars=_inputs().spx_bars, generated="2026-11-06T18:20:00")
    assert d["found"] and d["asof"] == "2026-11-06" and len(d["bars"]) == 60
    assert d["bars"][-1]["d"] == "2026-11-06" and d["prior_asof"] == "2026-11-05"
    assert d["score"]["key"]["n"] == 3 and "Track record" in d["score"]["headline"]
    assert all(abs(z["centre"] - d["spot"]) <= d["spot"] * 0.08 for z in d["zones"])
    lv = [x for z in d["zones"] for x in z["levels"]]
    assert any("oi" in x for x in lv if x["layer"] == "oi")  # detail fields kept for tooltips
