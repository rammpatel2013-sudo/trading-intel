"""Tests for the sentiment snapshot job — pure, no Postgres.

``build_rows`` runs against a fake CVForge client (canned FMP payloads per endpoint);
``run`` uses a recording session (the Postgres ``pg_insert`` statements are captured, not
compiled), so no live DB / creds are needed — mirrors ``test_letf_flows``.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.dialects.postgresql.dml import Insert

from trading_intel.scheduler.jobs.sentiment import build_rows, run

_AS_OF = date(2026, 7, 17)

_PAYLOADS: dict[str, object] = {
    "institutional-ownership/symbol-ownership": [
        {
            "ownershipPercent": 45.0,
            "investorsHolding": 3723,
            "numberOf13Fshares": 1.26e9,
            "numberOf13FsharesChange": 29.3e6,
            "newPositions": 210,
            "closedPositions": 180,
            "putCallRatio": 0.8,
        }
    ],
    "price-target-consensus": {"targetConsensus": 252.0, "targetHigh": 400.0, "targetLow": 164.0},
    "grades-consensus": {
        "strongBuy": 20,
        "buy": 17,
        "hold": 5,
        "sell": 1,
        "strongSell": 0,
        "consensus": "Buy",
    },
    "quote": [{"symbol": "ORCL", "price": 133.6}],
}


class _FakeClient:
    """Fake CVForge client: returns a canned payload per FMP endpoint."""

    def __init__(self, payloads: dict[str, object]) -> None:
        self.payloads = payloads
        self.calls: list[str] = []

    def fmp(self, endpoint: str, params: dict | None = None) -> object:
        self.calls.append(endpoint)
        return self.payloads.get(endpoint)


class _Settings:
    def __init__(self) -> None:
        self.sentiment_symbols = ["ORCL"]


class _RecordingSession:
    def __init__(self) -> None:
        self.executed: list = []
        self.commits = 0

    def execute(self, stmt) -> None:
        self.executed.append(stmt)

    def commit(self) -> None:
        self.commits += 1


def test_build_rows_maps_and_derives():
    rows = build_rows(_FakeClient(_PAYLOADS), ["ORCL"], as_of=_AS_OF)
    assert len(rows) == 1
    r = rows[0]
    assert r["symbol"] == "ORCL" and r["ts"] == _AS_OF
    assert r["inst_pct"] == 45.0 and r["num_analysts"] == 43
    assert r["rating_consensus"] == "Buy" and r["source"] == "cvforge-fmp"
    assert abs(r["buy_share"] - 37 / 43) < 1e-9
    assert abs(r["pt_upside_pct"] - (252.0 / 133.6 - 1.0)) < 1e-9


def test_run_seeds_tickers_upserts_and_commits():
    session = _RecordingSession()
    run(session, _FakeClient(_PAYLOADS), settings=_Settings())

    inserts = [e for e in session.executed if isinstance(e, Insert)]
    # One ticker-seed insert + one snapshot upsert.
    assert len(inserts) == 2
    assert {ins.table.name for ins in inserts} == {"tickers", "sentiment_snapshots"}
    assert session.commits == 1
