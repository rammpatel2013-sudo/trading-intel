"""Smoke tests: scripts/confluence_report.py and scripts/confluence_score.py on a seeded DB."""

from __future__ import annotations

import json
from datetime import date

from tests.confluence_fixtures import seeded_session
from trading_intel.api import confluence as api
from trading_intel.market.confluence_build import inputs_to_json
from trading_intel.scheduler.jobs._script_report import load_script


def test_report_writes_json_and_static_html(tmp_path) -> None:
    s = seeded_session()
    rpt = load_script("confluence_report")
    res = rpt.build(date(2026, 11, 6), out_dir=tmp_path, session=s)
    jp, hp = res["paths"]["json"], res["paths"]["html"]
    assert jp.endswith("confluence_20261106.json") and hp.endswith("SPX_confluence_2026-11-06.html")
    doc = open(hp, encoding="utf-8").read()
    assert "<script" not in doc and "<svg" in doc
    assert "Positioning read, not a trade signal" in doc
    assert "Data gaps" in doc and "What this means" in doc and "Track record" in doc
    assert "font:18px" in doc  # large body text
    assert "not on anyone" in doc and "vol-shock peak (info)" in doc
    r = json.loads(open(jp, encoding="utf-8").read())
    assert r["as_of"] == "2026-11-06"


def test_report_no_html_and_past_date(tmp_path) -> None:
    s = seeded_session()
    rpt = load_script("confluence_report")
    res = rpt.build(date(2026, 11, 5), out_dir=tmp_path, session=s, html_out=False)
    assert "html" not in res["paths"] and res["readout"]["as_of"] == "2026-11-05"
    assert not list(tmp_path.glob("*.html"))


def test_report_from_fixture_file(tmp_path) -> None:
    s = seeded_session()
    inp = api.load_inputs(s, date(2026, 11, 6))
    fx = tmp_path / "inputs.json"
    fx.write_text(json.dumps(inputs_to_json(inp)))
    rpt = load_script("confluence_report")
    res = rpt.build(out_dir=tmp_path / "out", fixture=str(fx))
    assert res["readout"]["zones"] == api.compute_for_day(s, date(2026, 11, 6),
                                                          out_dir=tmp_path / "none")["zones"]


def test_score_backfill_then_score(tmp_path) -> None:
    s = seeded_session(bars_after=6)
    sc = load_script("confluence_score")
    res = sc.run(s, tmp_path, do_backfill=True)
    assert sorted(p.name for p in tmp_path.glob("confluence_*.json")) == [
        "confluence_20261104.json", "confluence_20261105.json", "confluence_20261106.json"]
    assert res["n_days_stored"] == 3 and res["days_scored"]["1"] == 3
    assert (tmp_path / "score.json").exists()
    assert "Track record so far" in (tmp_path / "SPX_confluence_2026-11-06.html").read_text(encoding="utf-8")
    later = json.loads((tmp_path / "confluence_20261105.json").read_text())
    assert later["prior_as_of"] == "2026-11-04"  # backfill runs oldest first
    # re-run without --force keeps files; nothing rebuilt
    assert sc.backfill(s, tmp_path, log=lambda *_: None) == []


def test_report_writes_cockpit_json(tmp_path) -> None:
    s = seeded_session()
    rpt = load_script("confluence_report")
    ck = tmp_path / "cvx" / "confluence.json"
    rpt.build(date(2026, 11, 5), out_dir=tmp_path, session=s, html_out=False, cockpit=False)
    res = rpt.build(date(2026, 11, 6), out_dir=tmp_path, session=s, html_out=False, cockpit=ck)
    d = json.loads(ck.read_text())
    assert res["paths"]["cockpit"] == str(ck)
    assert d["found"] and d["asof"] == "2026-11-06" and d["prior_asof"] == "2026-11-05"
    assert 0 < len(d["bars"]) <= 60 and set(d["bars"][0]) == {"d", "o", "h", "l", "c"}
    assert all(abs(z["centre"] - d["spot"]) <= d["spot"] * 0.08 for z in d["zones"])
    assert d["prior_zones"] and "info_note" in d
    # a --date rebuild of a past day leaves the live cockpit file alone by default
    before = ck.read_text()
    rpt.build(date(2026, 11, 4), out_dir=tmp_path, session=s, html_out=False)
    assert ck.read_text() == before
