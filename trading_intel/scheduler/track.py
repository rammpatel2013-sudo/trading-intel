"""Run one scheduled job and record it in ``pipeline_runs`` (2026-11-19, CLAUDE.md rule 5).

``scripts/nas/run_job.sh`` calls ``python -m trading_intel.scheduler.track <job>`` instead of running the job
module directly. This wrapper:

1. writes a ``pipeline_runs`` row (status ``running``),
2. runs ``trading_intel.scheduler.jobs.<job>`` exactly as ``python -m`` would (``__main__``),
3. updates the row with the finish time, ``ok`` / ``failed`` and the error text, and exits with the job's code.

The bookkeeping never changes what the job does: if the database cannot be reached for the row, the job still
runs and the wrapper only prints a warning. A job can report its row count with ``report(rows_written=...)``.
"""

from __future__ import annotations

import os
import runpy
import socket
import sys
import traceback

_REPORT: dict = {}


def report(*, rows_attempted: int | None = None, rows_written: int | None = None) -> None:
    """Called by a job (optional) to put its row counts on its pipeline_runs row."""
    if rows_attempted is not None:
        _REPORT["rows_attempted"] = int(rows_attempted)
    if rows_written is not None:
        _REPORT["rows_written"] = int(rows_written)


def _start(job: str):
    try:
        from trading_intel.config import get_settings
        from trading_intel.memory.db import make_session_factory
        from trading_intel.memory.models import PipelineRun
        from trading_intel.timeutils import eastern_now

        factory = make_session_factory(get_settings())
        with factory() as s:
            row = PipelineRun(job=job[:64], run_date=eastern_now().date(), started_at=eastern_now(),
                              status="running", host=socket.gethostname()[:64])
            s.add(row)
            s.commit()
            return factory, row.id
    except Exception as exc:  # noqa: BLE001 - bookkeeping must never stop the job
        print(f"[track] pipeline_runs start not recorded: {exc}", file=sys.stderr)
        return None, None


def _finish(factory, run_id, status: str, error: str | None) -> None:
    if factory is None or run_id is None:
        return
    try:
        from trading_intel.memory.models import PipelineRun
        from trading_intel.timeutils import eastern_now

        with factory() as s:
            row = s.get(PipelineRun, run_id)
            if row is None:
                return
            row.finished_at = eastern_now()
            row.status = status
            row.error = (error or None) and error[-4000:]
            row.rows_attempted = _REPORT.get("rows_attempted")
            row.rows_written = _REPORT.get("rows_written")
            s.commit()
    except Exception as exc:  # noqa: BLE001
        print(f"[track] pipeline_runs finish not recorded: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: python -m trading_intel.scheduler.track <job_module> [args...]", file=sys.stderr)
        return 2
    job, rest = argv[0], argv[1:]
    factory, run_id = (None, None) if os.environ.get("TI_TRACK_OFF") else _start(job)
    sys.argv = [f"trading_intel.scheduler.jobs.{job}", *rest]
    code, err = 0, None
    try:
        runpy.run_module(f"trading_intel.scheduler.jobs.{job}", run_name="__main__", alter_sys=True)
    except SystemExit as exc:
        c = exc.code
        code = c if isinstance(c, int) else (0 if c is None else 1)
        if code:
            err = f"SystemExit({c})"
    except BaseException:  # noqa: BLE001 - recorded, then re-raised as a non-zero exit
        code, err = 1, traceback.format_exc()
        traceback.print_exc()
    _finish(factory, run_id, "ok" if code == 0 else "failed", err)
    return code


if __name__ == "__main__":
    sys.exit(main())
