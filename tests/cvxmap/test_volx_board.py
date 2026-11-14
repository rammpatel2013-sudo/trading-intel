# ruff: noqa: N812  (house style: L / W / V / X module aliases)
"""volx vol board (2026-11-14): cross-asset vol rows, vol-fund rows, the spike-fade states and the
vf-v2 signal state - pure functions with synthetic history, no network."""
import datetime
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "cvxmap"))
import volx as X  # noqa: E402

TODAY = datetime.date(2026, 11, 13)


def _days(n: int) -> list[datetime.date]:
    return [TODAY - datetime.timedelta(days=n - i) for i in range(n)]


def _hist(vix: list[float], v9: list[float] | None = None, extra: dict | None = None) -> dict:
    ds = _days(len(vix))
    h = {"VIX": list(zip(ds, vix, strict=True))}
    if v9 is not None:
        h["VIX9D"] = list(zip(ds, v9, strict=True))
    for s, vals in (extra or {}).items():
        h[s] = list(zip(ds[-len(vals):], vals, strict=True))
    return h


def test_board_rows_ratio_percentile_and_close_fallback():
    vix = [20.0] * 300
    vxn = [24.0 + 0.01 * i for i in range(300)]          # rising: today's level is the year high
    h = _hist(vix, extra={"VXN": vxn, "OVX": [40.0] * 300, "SKEW": [140.0] * 300})
    q = {"VIX": {"v": 20.0, "t": "2026-11-13T15:00:00"},
         "VXN": {"v": 30.0, "prev": vxn[-1], "t": "2026-11-13T15:00:00"}}
    b = X.board(h, q, TODAY)
    rows = {r["s"]: r for r in b["rows"]}
    assert rows["VXN"]["src"] == "live" and rows["VXN"]["ratio"] == pytest.approx(1.5)
    assert rows["VXN"]["pct"] == 100.0 and rows["VXN"]["ratio_pct"] == 100.0
    assert rows["VXN"]["d1"] == pytest.approx(30.0 - vxn[-1], abs=0.01)
    assert len(rows["VXN"]["spark"]) == 21                     # 20 closes + the live value
    assert rows["OVX"]["src"] == "close" and rows["OVX"]["d1"] == 0.0 and len(rows["OVX"]["spark"]) == 20
    assert rows["SKEW"]["ratio"] is None                       # SKEW is not a vol level: no ratio to VIX
    assert "RVX" not in rows                                   # no quote and no history -> no row
    assert b["bt"]["rows"] and b["bt"]["asof"] == "2026-11-14"


def test_board_fund_rows_percent_changes():
    h = _hist([20.0] * 30, extra={"UVXY": [20.0] * 30, "SVXY": [50.0] * 30})
    q = {"UVXY": {"v": 18.0, "prev": 20.0, "t": "2026-11-13T15:00:00"}}
    e = {r["s"]: r for r in X.board(h, q, TODAY)["etp"]}
    assert e["UVXY"]["d1"] == pytest.approx(-10.0) and e["UVXY"]["d21"] == pytest.approx(-10.0)
    assert e["UVXY"]["lev"] == 1.5 and e["SVXY"]["lev"] == -0.5 and e["SVXY"]["src"] == "close"


def test_board_after_the_bell_uses_closes_not_stale_quotes():
    """Saturday 11/14: Cboe's UVXY quote said current 19.00 / close 17.99 (history through 11/12); VXN's quote
    is the 11/13 close the history already holds and its prev_day_close had rolled to the same value."""
    sat = datetime.date(2026, 11, 14)
    ds = [sat - datetime.timedelta(days=30 - i) for i in range(30)]          # ... 11/13
    h = {"VIX": [(d, 20.0) for d in ds], "VXN": [(d, 28.0 + 0.1 * i) for i, d in enumerate(ds)],
         "UVXY": [(d, 17.35) for d in ds[:-1]]}                                # fund history lags: ... 11/12
    q = {"UVXY": {"v": 18.9992, "prev": 17.35, "c": 17.99, "t": "2026-11-13T15:59:57"},
         "VXN": {"v": 30.9, "prev": 30.9, "c": 30.9, "t": "2026-11-13T00:00:00-06:00"}}
    b = X.board(h, q, sat)
    e = {r["s"]: r for r in b["etp"]}["UVXY"]
    assert e["v"] == 17.99 and e["src"] == "close"
    assert e["d1"] == pytest.approx(100 * (17.99 / 17.35 - 1), abs=0.01)
    assert len(e["spark"]) == 21 and e["spark"][-1] == 17.99                   # 20 closes + the 11/13 close
    r = {x["s"]: x for x in b["rows"]}["VXN"]
    assert r["v"] == pytest.approx(30.9) and r["src"] == "close" and r["d1"] == pytest.approx(0.1)
    assert r["d5"] == pytest.approx(0.5) and len(r["spark"]) == 20


def test_fade_state_buckets_first_close_and_release():
    # 40 calm sessions, a spike to 31 that lasts 2 days, then back down; VIX9D above VIX for 3 days
    vix = [16.0] * 40 + [26.0, 31.0, 24.0, 20.0, 18.0]
    v9 = [15.0] * 40 + [29.0, 34.0, 25.0, 19.0, 17.0]
    f = X.fade_state(_hist(vix, v9), {}, TODAY)
    assert f["vix"] == 18.0 and f["bucket"] == "under 20" and f["live"] is False
    assert f["c25"]["vix"] == 26.0 and f["c25"]["ago"] == 4
    assert f["c30"]["vix"] == 31.0 and f["c30"]["ago"] == 3
    assert f["release"]["days"] == 3 and f["run_above1"] == 0      # 3 closes above 1.00 then 19/20 <= 1
    assert f["v9r"] == pytest.approx(17 / 18, abs=1e-3)


def test_fade_state_live_quote_and_buckets():
    h = _hist([22.0] * 30, [21.0] * 30)
    q = {"VIX": {"v": 35.0, "t": "2026-11-14T10:00:00"}, "VIX9D": {"v": 38.0, "t": "2026-11-14T10:00:00"},
         "VIX3M": {"v": 28.0}}
    f = X.fade_state(h, q, datetime.date(2026, 11, 14))
    assert f["live"] is True and f["bucket"] == "30 to 40" and f["c30"]["ago"] == 0
    assert f["run_above1"] == 1 and f["vix_vix3m"] == pytest.approx(1.25)
    assert X.fade_state({}, {}, TODAY) is None


def test_board_is_safe():
    assert X._safe_board({"VIX": "not a list"}, {}, TODAY) is None      # error -> None, no crash
    assert not hasattr(X, "copy_page")                                 # standalone page retired 11/14


def _spike_hist(n_after: int = 3) -> dict:
    """calm, a close at 26, VIX9D above VIX for 3 closes, back under on the last day (a fire)."""
    vix = [18.0] * 30 + [26.0, 24.0, 22.0] + [20.0] * n_after
    v9 = [17.0] * 30 + [27.0, 25.0, 23.0] + [19.0] * n_after
    return _hist(vix, v9, {"VVIX": [100.0] * 32 + [115.0] * (1 + n_after)})


def test_fade_signal_fire_from_history():
    h = _spike_hist(1)
    s = X.fade_signal(h, {}, TODAY)
    st = s["state"]
    assert s["rule"].startswith("vf-v2") and s["provisional"] is False
    assert st["phase"] == "FIRE" and st["fire"]["strength"] == "strong"
    assert s["headline"].startswith("FIRE") and s["fires"][-1]["d"] == h["VIX"][-1][0].isoformat()
    assert s["evidence"]["rows"]["all_20"] and s["hold"] == 20 and s["strong_vvix"] == 110.0
    assert len(s["path"]) == len(h["VIX"]) and s["path"][-1][4] == "FIRE" and s["path"][-1][3] == 115.0


def test_fade_signal_live_quote_is_provisional():
    h = _spike_hist(0)                                                  # history ends on a VIX9D>VIX day
    for k in h:
        h[k] = [(d - datetime.timedelta(days=1), v) for d, v in h[k]]    # ... through yesterday
    q = {"VIX": {"v": 20.0, "t": "2026-11-13T14:00:00"}, "VIX9D": {"v": 19.0, "t": "2026-11-13T14:00:00"},
         "VVIX": {"v": 105.0}}
    s = X.fade_signal(h, q, TODAY)
    assert s["provisional"] is True and s["state"]["d"] == TODAY.isoformat()
    assert s["state"]["phase"] == "FIRE" and s["state"]["fire"]["strength"] == "normal"
    assert X.fade_signal({}, {}, TODAY) is None


def test_fade_signal_weekend_quote_not_a_new_day():
    h = {k: [(d + datetime.timedelta(days=1), v) for d, v in rows] for k, rows in _spike_hist(1).items()}
    q = {"VIX": {"v": 20.0, "t": "2026-11-13T16:15:00"}}               # Friday's close, history has it
    s = X.fade_signal(h, q, datetime.date(2026, 11, 14))
    assert s["provisional"] is False and s["state"]["d"] == TODAY.isoformat()


def test_board_carries_the_signal_and_vvix_is_pulled():
    assert "VVIX" in X.B_IDX
    b = X.board(_spike_hist(1), {}, TODAY)
    assert b["sig"]["state"]["phase"] == "FIRE"
