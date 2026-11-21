"""pos_ledger --morning (2026-11-21): today's OI goes into the ledger once the first SPX pull lands."""
import datetime
import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "cvxmap"))
import pos_ledger as pl

DAY = datetime.date(2026, 11, 23)


def _raw(tmp_path, recs):
    p = tmp_path / "raw_20261123.ndjson.gz"
    with gzip.open(p, "wt", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r) + "\n")
    return str(p)


def test_first_idx_pull_ready(tmp_path):
    assert not pl.first_idx_pull_ready(str(tmp_path / "missing.gz"), DAY)
    p = _raw(tmp_path, [{"pull": "ext", "symbols": ["SPX"], "ts": "2026-11-23T09:12:00"}])
    assert not pl.first_idx_pull_ready(p, DAY)                      # ext pull only
    p = _raw(tmp_path, [{"pull": "idx", "symbols": ["SPX"], "ts": "2026-11-20T15:59:00"}])
    assert not pl.first_idx_pull_ready(p, DAY)                      # wrong day
    p = _raw(tmp_path, [{"pull": "idx", "symbols": ["SPX", "SPY"], "ts": "2026-11-23T09:12:03"}])
    assert pl.first_idx_pull_ready(p, DAY)


def test_morning_refresh_keeps_health_and_gives_up(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "DATA", str(tmp_path))
    calls = []
    monkeypatch.setattr(pl, "reconcile_oi", lambda engine, d: calls.append(("rec", d)) or 7)
    monkeypatch.setattr(pl, "export_ledger_json", lambda engine, d, h=None: calls.append(("exp", d, h)))
    (tmp_path / "ledger.json").write_text(json.dumps({"as_of": "2026-11-23", "health": {"n": 5}}))
    # no raw yet -> gives up without touching the ledger
    assert pl.morning_refresh(None, DAY, wait_s=0, poll_s=1) == 0 and calls == []
    _raw(tmp_path, [{"pull": "idx", "symbols": ["SPX"], "ts": "2026-11-23T09:12:03"}])
    assert pl.morning_refresh(None, DAY, wait_s=0, poll_s=1) == 7
    assert calls == [("rec", DAY), ("exp", DAY, {"n": 5})]
