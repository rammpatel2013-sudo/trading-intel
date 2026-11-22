# ruff: noqa: N812
"""wide_feed --vix-only (2026-11-21, VIX map C4 backfill): replay only the VIX pulls of a stored raw file into
vixmap_YYYYMMDD.json with the volume-by-level block, oldest day first so each day's zones see the days before."""
import datetime
import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "cvxmap"))
import wide_feed as W

from tests.cvxmap.test_vix_map import make_vix_rec


def _raw(path: Path, day: datetime.date, vix0: float) -> None:
    with open(path, "wb") as fh:
        for i in range(6):
            now = datetime.datetime(day.year, day.month, day.day, 9, 30) + datetime.timedelta(minutes=i)
            rec = make_vix_rec(now.isoformat(), vix=vix0 + 0.1 * i)
            fh.write(gzip.compress((json.dumps(rec) + "\n").encode()))      # one gzip member per write, like the collector
            fh.write(gzip.compress((json.dumps({"ts": now.isoformat(), "pull": "idx", "data": []}) + "\n").encode()))


def test_vix_only_rebuild_writes_vp_and_zones_from_earlier_days(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "DATA", str(tmp_path))
    _raw(tmp_path / "raw_20261105.ndjson.gz", datetime.date(2026, 11, 5), 18.0)
    _raw(tmp_path / "raw_20261106.ndjson.gz", datetime.date(2026, 11, 6), 17.2)
    assert W.rebuild_vix("20261105") == 0
    assert W.rebuild_vix("20261106") == 0
    a = json.loads((tmp_path / "vixmap_20261105.json").read_text())
    b = json.loads((tmp_path / "vixmap_20261106.json").read_text())
    assert a["vp"]["lv"] and a["rng"] == [18.0, 18.5] and a["zones"]["days"] == 0
    assert b["zones"]["days"] == 1 and b["zones"]["from"] == "2026-11-05" and b["zones"]["zones"]
    assert not list(tmp_path.glob("wide_2*.json"))                    # vix only: no Orbs day file written
    assert len(b["px"]) == 6 and b["books"]


def test_vix_only_day_without_vix_pulls(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "DATA", str(tmp_path))
    (tmp_path / "raw_20261107.ndjson.gz").write_bytes(gzip.compress(b'{"ts":"2026-11-07T09:31:00","pull":"idx"}\n'))
    assert W.rebuild_vix("20261107") == 1
