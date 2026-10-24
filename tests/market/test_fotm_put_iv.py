"""Unit tests for the pure F-OTM put-IV floor tracker."""

from __future__ import annotations

from datetime import date, timedelta

from trading_intel.market import fotm_put_iv as f


def _rows(ivs, spots, start=date(2026, 6, 1), n_exp=32):
    return [
        dict(ts=start + timedelta(days=i), iv_put_25d=iv, spot=sp, n_expiries=n_exp)
        for i, (iv, sp) in enumerate(zip(ivs, spots))
    ]


def test_clean_drops_bad_pulls_and_dedups():
    rows = _rows([0.14, 0.15, 0.16], [700, 701, 702])
    rows[1]["n_expiries"] = 3  # bad pull
    rows.append(dict(ts=date(2026, 6, 3), iv_put_25d=0.17, spot=703, n_expiries=32))  # dup day
    pts = f.clean_rows(rows)
    assert [p.ts for p in pts] == [date(2026, 6, 1), date(2026, 6, 3)]
    assert pts[-1].iv == 0.17  # last wins


def test_states_floor_warn_neutral():
    ivs = [0.15, 0.125, 0.18, 0.18]
    spots = [700, 700, 700, 650]  # last: 7% off high → not WARN
    pts = f.label_states(f.clean_rows(_rows(ivs, spots)), high_lookback=2)
    assert [p.state for p in pts] == ["NEUTRAL", "FLOOR", "WARN", "NEUTRAL"]


def test_clusters_merge_and_forward_stats():
    # floor days 1,2 then 4 (gap 2 → merged) then 30 (separate)
    n = 60
    ivs = [0.15] * n
    for i in (1, 2, 4, 30):
        ivs[i] = 0.12
    ivs[8] = 0.18  # WARN 4 sessions after last floor day (idx 4)
    spots = [700 + i for i in range(n)]  # grinding up → near highs
    v = f.build_view(_rows(ivs, spots), high_lookback=5)
    cl = v["clusters"]
    assert len(cl) == 2
    assert cl[0].n_touches == 3 and cl[0].end == date(2026, 6, 5)
    assert cl[0].days_to_warn == 4
    assert abs(cl[0].fwd["floor_16d"] - (720 / 704 - 1)) < 1e-9
    assert abs(cl[0].fwd["warn_20d"] - (728 / 708 - 1)) < 1e-9
    assert cl[0].max_dd_30d is not None and cl[0].max_dd_30d > 0  # monotonic up → no dd
    assert cl[1].n_touches == 1 and cl[1].warn_date is None


def test_read_header():
    ivs = [0.12, 0.15, 0.18]
    spots = [700, 701, 702]
    v = f.build_view(_rows(ivs, spots), high_lookback=3)
    r = v["read"]
    assert r["state"] == "WARN"
    assert r["days_since_floor"] == 2
    assert r["last_floor_iv"] == 0.12
    assert r["warn_run_days"] == 1


def test_empty():
    v = f.build_view([])
    assert v["read"]["state"] == "NO DATA" and v["clusters"] == []


def test_warn_needs_full_lookback_window():
    # IV high + SPY at its (short) high on day 3: no WARN until 60 sessions exist
    pts = f.label_states(f.clean_rows(_rows([0.15, 0.15, 0.18], [700, 701, 702])))
    assert [p.state for p in pts] == ["NEUTRAL", "NEUTRAL", "NEUTRAL"]


def test_splice_chart_before_db_and_db_wins():
    chart = _rows([0.12, 0.13, 0.14, 0.15], [600, 601, 602, 603], n_exp=None)
    db = _rows([0.20, 0.21], [700, 701], start=date(2026, 6, 3))  # overlaps chart days 3-4
    rows = f.splice(chart, db)
    assert [r["source"] for r in rows] == ["chart", "chart", "db", "db"]
    assert rows[2]["iv_put_25d"] == 0.20
    v = f.build_view(rows)
    assert v["read"]["db_start"] == "2026-06-03" and v["read"]["first_date"] == "2026-06-01"
    assert v["clusters"][0].source == "chart"


def test_splice_without_db_returns_chart():
    chart = _rows([0.12], [600], n_exp=None)
    assert [r["source"] for r in f.splice(chart, [])] == ["chart"]


def test_chart_history_file_is_sane():
    import csv
    from pathlib import Path

    p = Path(__file__).resolve().parents[2] / "data" / "reference" / "fotm_put_iv_chart_history.csv"
    rows = list(csv.DictReader(p.open(encoding="utf-8")))
    assert rows[0]["date"] == "2024-10-16" and len(rows) > 450
    ivs = [float(r["put_iv"]) for r in rows]
    spys = [float(r["spy"]) for r in rows]
    assert 0.09 < min(ivs) < 0.12 and 0.55 < max(ivs) < 0.70  # vendor labels: 11% floor, 63% Apr-25 peak
    assert 480 < min(spys) < 510 and max(spys) < 800
