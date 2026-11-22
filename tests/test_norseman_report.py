"""Norseman divergence page (2026-11-22)."""
import importlib.util
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _m():
    spec = importlib.util.spec_from_file_location("nr_rep", ROOT / "scripts" / "norseman_report.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _days(n):
    d0 = date(2026, 6, 1)
    return [d0 + timedelta(days=i) for i in range(n)]


def test_rails_divergence_status_and_page() -> None:
    m = _m()
    days = _days(60)
    spx = [(d, 100 + i) for i, d in enumerate(days)]                     # SPX high on the last day
    rsp = [(d, 50 + (i if i < 30 else 60 - i)) for i, d in enumerate(days)]  # peaked mid-way (30 sessions earlier)
    iwm = [(d, 20 + i * 0.5) for i, d in enumerate(days)]                # confirmed
    ad = [(d, -500 + i * 10) for i, d in enumerate(days)]                # negative A-D line, confirmed
    raw = {"SPX": spx, "RSP": rsp, "IWM": iwm, "A-D 500": ad}
    rails = m.read_rails(raw, days[-1], days)
    rsp_r = next(r for r in rails if r["key"] == "RSP")
    assert rsp_r["diverging"] and rsp_r["gap"] == 29
    assert not next(r for r in rails if r["key"] == "A-D 500")["diverging"]
    color, why = m.status(below_line=False, week_close=False, n_closes=0, dist_to_line=0.12, tested_recently=False,
                          rails=rails)
    assert color == "orange" and "RSP" in why[0]
    assert m.should_send("orange", False) and not m.should_send("green", False) and m.should_send("green", True)
    red, _ = m.status(below_line=True, week_close=True, n_closes=1, dist_to_line=-0.02, tested_recently=True, rails=rails)
    assert red == "red"
    assert m.resolution(below_line=True, rails=rails)[0] == "Resolving BEARISH"
    corr = {k: m.rolling_corr(v, spx, diff=k.startswith("A-D")) for k, v in raw.items() if k != "SPX"}
    doc = m.page(as_of=days[-1], spx_close=159, line=143.1, dist=0.11, ath=159, ath_date=days[-1], color=color, why=why,
                 res=m.resolution(below_line=False, rails=rails), rails=rails,
                 series_scaled={k: m.scaled(v) for k, v in raw.items()}, days=days, corr=corr, window_start=days[0],
                 n_closes=0)
    assert "<script" not in doc and "ORANGE" in doc and "topped 29 sessions BEFORE SPX" in doc and "<polyline" in doc


def test_green_when_all_confirm() -> None:
    m = _m()
    days = _days(30)
    raw = {"SPX": [(d, 100 + i) for i, d in enumerate(days)], "RSP": [(d, 50 + i) for i, d in enumerate(days)]}
    rails = m.read_rails(raw, days[-1], days)
    c, _ = m.status(below_line=False, week_close=False, n_closes=0, dist_to_line=0.10, tested_recently=False, rails=rails)
    assert c == "green" and m.resolution(below_line=False, rails=rails)[0] == "Confirmed"
