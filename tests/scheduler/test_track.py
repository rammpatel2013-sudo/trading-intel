"""pipeline_runs wrapper (2026-11-19): the job runs as __main__, its exit code and error are recorded."""

from __future__ import annotations

import sys
import types

from trading_intel.scheduler import track


def _fake_job(monkeypatch, body: str) -> None:
    mod = types.ModuleType("trading_intel.scheduler.jobs._track_probe")
    monkeypatch.setitem(sys.modules, "trading_intel.scheduler.jobs._track_probe", mod)
    code = compile(body, "_track_probe", "exec")

    def fake_run_module(name, run_name=None, alter_sys=False):
        assert name == "trading_intel.scheduler.jobs._track_probe" and run_name == "__main__"
        exec(code, {"__name__": "__main__"})

    monkeypatch.setattr(track.runpy, "run_module", fake_run_module)


def _record(monkeypatch):
    seen = {}
    monkeypatch.setattr(track, "_start", lambda job: ("F", 7))
    monkeypatch.setattr(track, "_finish", lambda f, i, status, err: seen.update(status=status, err=err, id=i))
    return seen


def test_ok_run(monkeypatch) -> None:
    _fake_job(monkeypatch, "x = 1\n")
    seen = _record(monkeypatch)
    assert track.main(["_track_probe"]) == 0 and seen == {"status": "ok", "err": None, "id": 7}


def test_failed_run_records_traceback(monkeypatch) -> None:
    _fake_job(monkeypatch, "raise RuntimeError('letf_flows: 0 rows')\n")
    seen = _record(monkeypatch)
    assert track.main(["_track_probe"]) == 1 and seen["status"] == "failed" and "0 rows" in seen["err"]


def test_sys_exit_code_kept(monkeypatch) -> None:
    _fake_job(monkeypatch, "import sys\nsys.exit(3)\n")
    seen = _record(monkeypatch)
    assert track.main(["_track_probe"]) == 3 and seen["status"] == "failed"


def test_db_down_never_stops_the_job(monkeypatch) -> None:
    _fake_job(monkeypatch, "x = 1\n")
    monkeypatch.setenv("TI_TRACK_OFF", "1")
    called = []
    monkeypatch.setattr(track, "_finish", lambda *a: called.append(a))
    assert track.main(["_track_probe"]) == 0 and called and called[0][0] is None
