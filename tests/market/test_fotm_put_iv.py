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
    pts = f.label_states(f.clean_rows(_rows(ivs, spots)))
    assert [p.state for p in pts] == ["NEUTRAL", "FLOOR", "WARN", "NEUTRAL"]


def test_clusters_merge_and_forward_stats():
    # floor days 1,2 then 4 (gap 2 → merged) then 30 (separate)
    n = 60
    ivs = [0.15] * n
    for i in (1, 2, 4, 30):
        ivs[i] = 0.12
    ivs[8] = 0.18  # WARN 4 sessions after last floor day (idx 4)
    spots = [700 + i for i in range(n)]  # grinding up → near highs
    v = f.build_view(_rows(ivs, spots))
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
    v = f.build_view(_rows(ivs, spots))
    r = v["read"]
    assert r["state"] == "WARN"
    assert r["days_since_floor"] == 2
    assert r["last_floor_iv"] == 0.12
    assert r["warn_run_days"] == 1


def test_empty():
    v = f.build_view([])
    assert v["read"]["state"] == "NO DATA" and v["clusters"] == []
