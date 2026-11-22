"""fear_width (2026-11-22): broad / narrow read of the fear, first VIX-30 days, Risk-page cards."""

import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import fear_width as fw  # noqa: E402
import risk_page as rp  # noqa: E402


def _series(n=1500):
    d0 = date(2018, 1, 1)
    days = [(d0 + timedelta(days=i)).isoformat() for i in range(n)]
    vix, cor, spx = {}, {}, {}
    for i, d in enumerate(days):
        v = 15 + (20 if 600 <= i < 610 or 1200 <= i < 1205 else 0) + (i % 7) * 0.3
        vix[d] = v
        cor[d] = 1.6 * v + (i % 5)          # correlation rises with the VIX
        spx[d] = 3000 * (1 + 0.0002 * i)
    cor[days[-1]] = 20.0                      # today: VIX high, correlation very low
    vix[days[-1]] = 36.0
    return vix, cor, spx


def test_narrow_read_and_touches():
    vix, cor, spx = _series()
    o = fw.build(vix, cor, spx)
    assert o["read"] == "Narrow" and o["pct"] <= 20 and o["near_q25"] < o["near_q75"]
    assert [t["date"][:4] for t in o["touches"]] == ["2019", "2021", "2022"]   # two spikes + today
    assert o["touches"][0]["r20"] is not None and o["touches"][-1]["r20"] is None
    assert len(o["gap"]) == len(o["gap_dates"]) == 252 and o["points"]


def test_short_history_is_empty():
    assert fw.build({"2026-01-02": 20}, {"2026-01-02": 30}, {"2026-01-02": 5000}) == {}


def test_cards_render(tmp_path):
    vix, cor, spx = _series()
    fuse = {"asof": "2022-02-08", "width": fw.build(vix, cor, spx)}
    st = {"asof": "2026-11-20", "cards": [], "rotation": [{"symbol": "XLK", "vs_spy20": -9.4, "quadrant": "Lagging",
                                                          "name": "Technology", "ratio": 98, "tail": [["d", 98, 99]]}],
          "sectors": [{"etf": "XLK", "name": "Technology", "n": 60, "s1": 5, "s2": 0, "s3": 26, "s4": 69,
                       "s2_20d_ago": 10, "dd20": 77.0, "r20": -13.5},
                      {"etf": "XLP", "name": "Consumer staples", "n": 30, "s1": 20, "s2": 40, "s3": 27, "s4": 13,
                       "s2_20d_ago": 30, "dd20": 16.0, "r20": 2.8}],
          "industries": [{"name": f"G{i}", "sector": "x", "n": 5, "s2": 80 - 10 * i, "s4": 10 * i,
                          "net": 80 - 20 * i, "net_20d_ago": 0} for i in range(8)]}
    for name, obj in (("fusetab.json", fuse), ("stagetab.json", st)):
        (tmp_path / name).write_text(json.dumps(obj), encoding="utf-8")
    rp.write(tmp_path)
    page = (tmp_path / "risk.html").read_text(encoding="utf-8")
    for want in ("Is this fear broad or narrow?", "Narrow", "past first days with VIX 30+",
                 "Where the stress is, and where it is not", "Stress is here:", "Technology", "Hardest-hit industry groups"):
        assert want in page
