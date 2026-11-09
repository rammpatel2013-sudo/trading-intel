"""get_confluence MCP tool: stored file vs on-the-fly compute, read-only."""

from __future__ import annotations

import json

from tests.confluence_fixtures import seeded_session
from trading_intel.api import confluence as api
from trading_intel.mcp import extra_tools as et


def test_computes_on_the_fly_when_no_file(tmp_path) -> None:
    s = seeded_session()
    r = et.get_confluence(s, out_dir=tmp_path)
    assert r["found"] and r["source"] == "computed" and r["as_of"] == "2026-11-06"
    assert r["disclaimer"] == "Positioning read, not a trade signal."
    assert set(r["layers"]) == {"greeks", "oi", "vix", "price", "crowd"}
    assert r["layers"]["oi"] and r["layers"]["vix"] and r["layers"]["crowd"]
    assert list(tmp_path.iterdir()) == []  # read-only: nothing written


def test_reads_stored_file_and_given_date(tmp_path) -> None:
    s = seeded_session()
    r = api.compute_for_day(s, api.book_days(s)[1], out_dir=tmp_path)
    api.write_json(r, tmp_path)
    got = et.get_confluence(s, day="2026-11-05", out_dir=tmp_path)
    assert got["source"] == "file" and got["as_of"] == "2026-11-05"
    # a different window recomputes instead of returning the stale file
    got2 = et.get_confluence(s, day="2026-11-05", window=40, out_dir=tmp_path)
    assert got2["source"] == "computed" and got2["window"] == 40


def test_prior_day_feeds_rate_of_change(tmp_path) -> None:
    s = seeded_session()
    api.write_json(api.compute_for_day(s, api.book_days(s)[1], out_dir=tmp_path), tmp_path)
    r = et.get_confluence(s, day="2026-11-06", out_dir=tmp_path)
    assert r["prior_as_of"] == "2026-11-05"
    assert any((z.get("roc") or {}).get("matched") for z in r["zones"])


def test_bad_inputs() -> None:
    s = seeded_session()
    assert et.get_confluence(s, day="11/06")["found"] is False
    assert et.get_confluence(s, symbol="QQQ")["found"] is False


def test_json_serialisable(tmp_path) -> None:
    s = seeded_session()
    json.dumps(et.get_confluence(s, out_dir=tmp_path))
