"""Zone cards (scripts/zone_cards.py + zone_cards_html.py) - 2026-11-22. Pure functions only, no network."""
from __future__ import annotations

import math
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import zone_cards as zc  # noqa: E402
import zone_cards_html as zh  # noqa: E402


def _days(n, start=date(2020, 1, 2)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _panel(n=600):
    ds = _days(n)
    X = {}
    X["SPX"] = [4000 * (1 + 0.0004 * i) * (1 + 0.01 * math.sin(i / 5)) for i in range(n)]
    X["VIX"] = [15 + 2 * math.sin(i / 7) + (12 if i in range(400, 410) else 0) for i in range(n)]
    X["VIXEQ"] = [v * 2.5 for v in X["VIX"]]
    X["VIX3M"] = [v + 2 for v in X["VIX"]]
    X["VIX9D"] = [v - 1 for v in X["VIX"]]
    X["VVIX"] = [90 + 5 * math.sin(i / 4) for i in range(n)]
    for k, b in (("COR1M", 20), ("COR3M", 22), ("COR10D", 8), ("COR90D", 35)):
        X[k] = [b + 3 * math.sin(i / 6 + b) for i in range(n)]
    X["MOVE"] = [None] * 100 + [100 + 5 * math.sin(i / 9) for i in range(n - 100)]
    X["SKEW"] = [140 + 4 * math.sin(i / 8) for i in range(n)]
    return ds, X


def test_zscore_matches_hand_calc():
    x = [float(i % 5) for i in range(60)] + [10.0]
    z, mu = zc.zscores(x)
    w = x[-51:-1]
    m = sum(w) / 50
    sd = math.sqrt(sum((v - m) ** 2 for v in w) / 50)
    assert abs(z[-1] - (10 - m) / sd) < 1e-12 and abs(mu[-1] - m) < 1e-12
    assert z[49] is None


def test_entries_cooldown_and_side():
    z = [0, 2.5, 0, 2.5, 0] + [0] * 10 + [2.5, -2.5, 0, -2.1]
    assert zc.entries(z, 1, 2.0) == [1, 15]          # second cross inside COOL ignored
    assert zc.entries(z, -1, 2.0) == [16]            # -2.1 at 18 comes after 0 but within COOL of 16


def test_outcomes_drop_flag():
    spx = [100.0] * 5 + [94.0] + [100.0] * 30
    oc = zc.outcomes(spx)
    assert oc[0][1] is True and oc[0][0] == 0.0       # 6% dip inside 20 days, flat end
    assert oc[6][1] is False and oc[-1] is None


def test_record_labels():
    base = {"up": 66.0, "drop": 15.0}
    assert zc.record(None, base)[0] == "weak"
    assert zc.record({"n": 40, "up": 66.0, "drop": 40.0}, base) == ("strong", "drop")
    assert zc.record({"n": 40, "up": 67.0, "drop": 16.0}, base)[0] == "weak"


def test_build_and_page(tmp_path):
    ds, X = _panel()
    o = zc.write(ds, X, tmp_path)
    ids = {c["id"] for c in o["cards"]}
    assert {"VIX", "COR1M", "EQ_RATIO", "IVRV", "MOVE", "SKEW"} <= ids
    order = [c["state"] for c in o["cards"]]
    assert order == sorted(order, key={"zone": 0, "near": 1, "none": 2}.get)
    page = (tmp_path / "zones.html").read_text(encoding="utf-8")
    assert "<script" not in page and page.count('class="card"') == len(o["cards"])
    assert (tmp_path / "zonecards.json").exists()
    for c in o["cards"]:
        lead, second, hist = zh.words(c)
        assert lead and second and hist
