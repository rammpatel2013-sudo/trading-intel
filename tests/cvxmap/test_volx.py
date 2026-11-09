# ruff: noqa: N812  (house style: L / W / V / X module aliases, F / K / T as in the maths)
"""volx (2026-11-09: its own container cvxmap-volx) - the gauge / cue / stack maths and one full step with the
network replaced, so the container entry is checked without leaving the machine."""
import datetime
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "cvxmap"))
import volx as X  # noqa: E402


def test_ema_and_labels():
    assert X.ema([1, 1, 1], 8) == [1, 1, 1]
    e = X.ema([0, 10], 3)
    assert e[1] == pytest.approx(5.0)
    assert X.label(True, False, False) == "CALM" and X.label(True, True, False) == "CALM*"
    assert [X.label(False, a, b) for a, b in ((0, 0), (1, 0), (1, 1))] == ["UNCONFIRMED", "MIXED", "CONFIRMED"]


def _hist(n=40, today=datetime.date(2026, 11, 6), vixm_trend=0.0):
    days = [today - datetime.timedelta(days=n - i) for i in range(n)]
    return {"VIXM": [(d, 30 + vixm_trend * i) for i, d in enumerate(days)], "VIXY": [(d, 20.0) for d in days],
            "VIX9D": [(d, 15.0) for d in days], "VIX": [(d, 16.0) for d in days],
            "SPX": [(d, 7000 + 5 * i) for i, d in enumerate(days)]}


def test_gauge_rows_today_provisional_and_labels():
    today = datetime.date(2026, 11, 6)
    q = {s: {"v": v, "t": "2026-11-06T12:00:00"} for s, v in
         (("VIXM", 31.0), ("VIXY", 20.0), ("VIX9D", 17.0), ("VIX", 16.0), ("SPX", 7100.0))}
    g = X.gauge(_hist(vixm_trend=0.05), q, today)
    assert g[-1]["d"] == "2026-11-06" and g[-1]["prov"] is True
    assert g[-1]["f_v9"] is True                         # 17 / 16 > 1
    assert X.gauge(_hist(vixm_trend=0.05), q, today, final=True)[-1]["prov"] is False
    assert X.gauge({}, q, today) is None                  # no history -> no gauge, no crash


def test_cues_and_stack():
    rows = [{"v9r": v, "spx": 7000, "s8": 6990} for v in (0.9, 1.2, 1.1, 1.0, 0.98)]
    c = X.cues(rows)
    assert c["peak5"] == 1.2 and c["close_shorts"] is True and c["reentry"] is True
    s = X.stack({"VIX": 16.0, "DSPX": 30.0, "VIXEQ": 34.0})
    assert s["share"] == pytest.approx(256 / (256 + 900), abs=1e-4) and s["gap"] == pytest.approx(34.0 - 34.0, abs=0.01)
    assert X.stack({"VIX": 0, "DSPX": 30}) is None


class _Resp:
    def __init__(self, js=None, text=""):
        self._js, self.text = js, text

    def json(self):
        return self._js


def test_container_entry_one_step_writes_both_files(tmp_path, monkeypatch):
    now = datetime.datetime(2026, 11, 6, 10, 41)
    monkeypatch.setattr(X, "now_et", lambda: now)
    hist_days = [now.date() - datetime.timedelta(days=40 - i) for i in range(40)]

    def fake_get(self, url, timeout=8):
        if "daily_prices" in url:
            return _Resp(text="DATE,OPEN,HIGH,LOW,CLOSE\n" + "\n".join(
                f"{d.month:02d}/{d.day:02d}/{d.year},1,1,1,{7000 if 'SPX' in url else 16}" for d in hist_days))
        if "historical" in url:
            return _Resp({"data": [{"date": d.isoformat(), "close": 30 if "VIXM" in url else 20} for d in hist_days]})
        sym = url.rsplit("/", 1)[-1].replace(".json", "").lstrip("_")
        return _Resp({"data": {"current_price": {"VIX": 16, "DSPX": 30, "VIXEQ": 34, "SPX": 7050}.get(sym, 18),
                               "prev_day_close": 17, "last_trade_time": "2026-11-06T10:26:00"}})

    monkeypatch.setattr(X.VolX, "_get", fake_get)
    assert X.main(["--once", "--out", str(tmp_path)]) == 0
    j = json.loads((tmp_path / "volx.json").read_text())
    assert j["q"]["VIX"]["v"] == 16 and j["gauge"] and j["stack_now"]["share"] > 0
    assert "vendor" in j["src"] and "Cboe" not in j["src"]          # no data-source name on screen
    d = json.loads((tmp_path / "volx_20261106.json").read_text())
    assert d["t"] == ["10:41"] and d["VIX"] == [16]


def test_network_down_keeps_running(tmp_path, monkeypatch):
    def boom(self, url, timeout=8):
        raise OSError("offline")

    monkeypatch.setattr(X.VolX, "_get", boom)
    v = X.VolX(str(tmp_path), lambda: datetime.datetime(2026, 11, 6, 10, 0))
    v.step()                                                       # no exception
    j = json.loads((tmp_path / "volx.json").read_text())
    assert j["q"] == {} and j["gauge"] is None and "offline" in (j["err"] or "")


def test_test_server_no_longer_runs_volx():
    src = (ROOT / "scripts" / "cvxmap" / "test_cockpit" / "serve_cvx_test.py").read_text()
    assert "import volx" not in src and "volx.start" not in src
    assert not (ROOT / "scripts" / "cvxmap" / "test_cockpit" / "volx.py").exists()
