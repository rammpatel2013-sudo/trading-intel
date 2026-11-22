"""Merged Risk page (scripts/risk_page.py + risk_page_html.py) - 2026-11-22. Pure functions, no network."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import risk_page as rp  # noqa: E402
import risk_page_html as rh  # noqa: E402


def _risk():
    dates = [f"2025-{m:02d}-{d:02d}" for m in range(1, 13) for d in range(1, 29)][:300]
    tile = lambda tid, grp, v, pct, lift: {  # noqa: E731
        "id": tid, "group": grp, "label": tid, "role": "score" if tid == "vix" else "context", "lift5": lift,
        "value": v, "unit": "", "pct": pct, "thr": 25.0, "long": [15 + (i % 20) for i in range(300)], "verdict": ""}
    return {
        "asof": "2026-11-20", "tile_dates": dates,
        "score": {"p5": 0.28, "p10": 0.21, "base5": 0.09, "base10": 0.067, "band_label": "Very high",
                  "inputs": [{"id": "vix", "label": "VIX", "value": 28.4}], "contrib": [0.5],
                  "similar": {"n": 300, "drop_rate": 0.27, "median_worst5": -1.6, "p10_worst5": -5.2}},
        "flags": {"false_allclear": {"on": True, "rate": 0.36}, "fear_release": {"on": False}},
        "hist": [[d, 5000 + i, 0.05 + i / 3000, 0.04, 0, 0] for i, d in enumerate(dates)],
        "tiles": [tile("vix", "score", 28.4, 0.97, 2.7), tile("pct200_low", "breadth", 49.0, 0.5, 2.3),
                  {"id": "kurt_bear", "group": "kurt", "role": "context", "value": 3, "rates": {"3": 0.14, "4": 0.31},
                   "parts": [{"name": "Gamma", "bear": True}, {"name": "Flow", "bear": False}]}],
        "volmon": {"combos": [{"id": "broad", "title": "Broad stress", "meaning": "m", "on": True, "color": "#f00",
                               "stats": {"episodes": 26, "up20": 0.66, "drop5": 0.27}},
                              {"id": "x", "title": "Other", "meaning": "m2", "on": False}]},
    }


def _zones():
    y = {"rank": 0.2, "q10": 1.0, "q25": 1.5, "q75": 2.5, "q90": 3.0, "base5": 0.09,
         "rec": {"top": {"n": 80, "rate": 0.2}, "bottom": {"n": 70, "rate": 0.25}}, "vals": [2.0] * 50, "v2wk": 2.4}
    return {"asof": "2026-11-20", "cards": [
        {"id": "EQ_RATIO", "value": 1.6, "z": -2.8, "year": y, "dates3y": ["2026-01-01"] * 50},
        {"id": "VXN", "value": 40.0, "z": 3.0, "year": {**y, "rank": 0.95}, "dates3y": ["2026-01-01"] * 50}]}


def test_build_order_groups_and_sides(tmp_path):
    o = rp.build(_risk(), {"phase": "TRANSMITTING", "phase5": "RELEASING", "score": {"F": 0, "T": 6, "R": 1},
                           "score5": {"F": 3, "T": 1, "R": 6}, "count": {"F": 8, "T": 7, "R": 7}}, _zones())
    ids = [c["id"] for c in o["cards"]]
    want = [k.split(":")[1] for _, k, *_ in rp.CARDS if k.split(":")[1] in ids]
    assert ids == want                                            # fixed reading order
    eq = next(c for c in o["cards"] if c["id"] == "EQ_RATIO")
    assert eq["two"] and abs(eq["rank"] - 0.8) < 1e-9 and eq["rate"] == 0.25   # both-ends gauge: low end used
    vxn = next(c for c in o["cards"] if c["id"] == "VXN")
    assert vxn["rank"] == 0.95 and vxn["rate"] == 0.2 and vxn["fast"]
    vix = next(c for c in o["cards"] if c["id"] == "vix")
    assert abs(vix["rate"] - 2.7 * 0.09) < 1e-9


def test_page_static_and_plain(tmp_path):
    for name, obj in (("risktab.json", _risk()), ("zonecards.json", _zones()),
                      ("fusetab.json", {"phase": "QUIET", "phase5": "QUIET", "score": {"F": 1, "T": 0, "R": 0},
                                        "score5": {}, "count": {"F": 8, "T": 7, "R": 7}})):
        (tmp_path / name).write_text(json.dumps(obj), encoding="utf-8")
    rp.write(tmp_path)
    page = (tmp_path / "risk.html").read_text(encoding="utf-8")
    assert page.count("<script") == 1                              # one small click-to-enlarge script
    assert "28%" in page and "false all-clear" in page and "Broad stress" in page
    assert "Very high for this year" in page
    assert page.count('class="card"') + page.count('class="card combo"') >= 4


def test_missing_files_still_build(tmp_path):
    rp.write(tmp_path)
    assert "Not built yet tonight" in (tmp_path / "risk.html").read_text(encoding="utf-8")


def test_record_words():
    c = {"id": "x", "rate": 0.27, "base": 0.09, "hi_bad": True, "src": "r", "role": "context"}
    assert "useful warning" in rh.record(c) and "27 times in 100" in rh.record(c)
    assert "not much of a warning" in rh.record({**c, "rate": 0.1})
