"""Tests for the LETF shares-outstanding job — pure, no Postgres.

``build_rows`` is tested against a fake ``EtfFlowSource``; ``run`` is exercised
with a recording session (the Postgres ``pg_insert`` statements are captured, not
compiled), so no live DB / creds are needed — mirrors ``test_iv_tenor_snapshots``.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.dialects.postgresql.dml import Insert

from trading_intel.clients import SharesSnapshot
from trading_intel.scheduler.jobs.letf_flows import build_rows, run

_AS_OF = date(2026, 7, 15)


class _Settings:
    def __init__(self) -> None:
        self.letf_symbols = ["SOXL", "SQQQ"]


class _FakeSource:
    """Fake ``EtfFlowSource``: returns a canned snapshot (or None) per symbol."""

    def __init__(self, snaps: dict[str, SharesSnapshot | None]) -> None:
        self.snaps = snaps
        self.calls: list[str] = []

    def shares_outstanding(self, symbol: str) -> SharesSnapshot | None:
        self.calls.append(symbol)
        return self.snaps.get(symbol)


# ── build_rows ──────────────────────────────────────────────────────────


def test_build_rows_one_row_per_symbol_with_snapshot():
    src = _FakeSource(
        {
            "SOXL": SharesSnapshot(
                "SOXL", 135_450_060, as_of=date(2026, 7, 14),
                float_shares=133_000_000, source="shares-float",
            ),
            "SQQQ": SharesSnapshot("SQQQ", 51_601_200, as_of=date(2026, 7, 14), source="quote"),
        }
    )
    rows = build_rows(src, _Settings(), as_of=_AS_OF)

    assert src.calls == ["SOXL", "SQQQ"]
    assert {r["symbol"] for r in rows} == {"SOXL", "SQQQ"}
    by_sym = {r["symbol"]: r for r in rows}
    assert by_sym["SOXL"]["shares_outstanding"] == 135_450_060
    assert by_sym["SOXL"]["float_shares"] == 133_000_000
    assert by_sym["SOXL"]["vendor_date"] == date(2026, 7, 14)
    assert by_sym["SOXL"]["source"] == "shares-float"
    for r in rows:
        assert r["ts"] == _AS_OF
        assert r["nav"] is None  # joined downstream from the price layer


def test_build_rows_skips_symbols_with_no_shares():
    src = _FakeSource({"SOXL": SharesSnapshot("SOXL", 135_450_060), "SQQQ": None})
    rows = build_rows(src, _Settings(), as_of=_AS_OF)
    assert src.calls == ["SOXL", "SQQQ"]
    assert [r["symbol"] for r in rows] == ["SOXL"]


# ── run orchestration ────────────────────────────────────────────────────


class _RecordingSession:
    def __init__(self) -> None:
        self.executed: list = []
        self.commits = 0

    def execute(self, stmt) -> None:
        self.executed.append(stmt)

    def commit(self) -> None:
        self.commits += 1


def test_run_seeds_tickers_upserts_and_commits():
    src = _FakeSource({"SOXL": SharesSnapshot("SOXL", 135_450_060)})
    settings = _Settings()
    settings.letf_symbols = ["SOXL"]
    session = _RecordingSession()

    run(session, src, settings=settings)

    inserts = [e for e in session.executed if isinstance(e, Insert)]
    # One ticker-seed insert + one snapshot upsert.
    assert len(inserts) == 2
    assert {ins.table.name for ins in inserts} == {"tickers", "letf_shares_snapshots"}
    assert session.commits == 1
