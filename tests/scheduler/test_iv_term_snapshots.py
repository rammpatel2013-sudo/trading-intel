"""Tests for the per-name constant-maturity IV-term job — pure, no Postgres.

``cm_interp`` (shared with iv_tenor) is checked directly; ``run`` is exercised with a
recording session and a monkeypatched ``build_rows`` (which otherwise queries
``oi_chain_eod``), so no live DB is needed — mirrors ``test_letf_flows`` / ``test_sentiment``.
"""

from __future__ import annotations

from datetime import date

import numpy as np
from sqlalchemy.dialects.postgresql.dml import Insert

from trading_intel.scheduler.jobs import iv_term_snapshots
from trading_intel.scheduler.jobs.iv_tenor_snapshots import cm_interp


def test_cm_interp_constant_maturity():
    dte = np.array([30.0, 90.0])
    vals = np.array([0.50, 0.60])
    iv60 = cm_interp(dte, vals, 60)
    assert iv60 is not None and 0.50 < iv60 < 0.60        # brackets the two nodes
    assert cm_interp(dte, vals, 10) is None               # outside span -> no extrapolation
    assert cm_interp(np.array([30.0]), np.array([0.5]), 30) is None  # <2 points


class _Settings:
    pass


class _RecordingSession:
    def __init__(self) -> None:
        self.executed: list = []
        self.commits = 0

    def execute(self, stmt) -> None:
        self.executed.append(stmt)

    def commit(self) -> None:
        self.commits += 1


_ROW = {
    "symbol": "ORCL",
    "ts": date(2026, 7, 17),
    "tenor_dte": 30,
    "iv_atm": 0.62,
    "iv_call_15d": 0.60,
    "iv_put_15d": 0.66,
    "iv_call_25d": 0.61,
    "iv_put_25d": 0.64,
    "spot": None,
    "n_expiries": 6,
}


def test_run_seeds_tickers_upserts_and_commits(monkeypatch):
    monkeypatch.setattr(iv_term_snapshots, "build_rows", lambda *a, **k: [_ROW])
    session = _RecordingSession()

    n = iv_term_snapshots.run(session, settings=_Settings())

    assert n == 1
    inserts = [e for e in session.executed if isinstance(e, Insert)]
    # One ticker-seed insert + one snapshot upsert (into the shared iv_tenor table).
    assert {ins.table.name for ins in inserts} == {"tickers", "iv_tenor_snapshots"}
    assert session.commits == 1
