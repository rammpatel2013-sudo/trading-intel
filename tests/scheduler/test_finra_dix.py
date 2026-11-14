"""FINRA DIX (2026-11-13 overnight): parse, compute, idempotent upsert."""
from datetime import date

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from trading_intel.clients.finra import parse_shvol as parse
from trading_intel.memory.models import FinraDixDaily
from trading_intel.scheduler.jobs import finra_dix

TXT = "Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market\n20261113|AAPL|400|0|1000|B,Q,N\n20261113|MSFT|300|0|1000|B,Q,N\n"


class _CV:
    def fmp(self, ep, params):
        if ep.startswith("sp500") or "constituent" in ep:
            return [{"symbol": "AAPL"}, {"symbol": "MSFT"}]
        return [{"symbol": "AAPL", "price": 200.0}, {"symbol": "MSFT", "price": 400.0}]


def test_parse_and_compute():
    sh = parse(TXT)
    assert sh["AAPL"] == (400.0, 1000.0)
    v = finra_dix.compute(sh, {"AAPL": 200.0, "MSFT": 400.0, "ZZZ": 5.0})
    assert abs(v["dix"] - (400 * 200 + 300 * 400) / (1000 * 200 + 1000 * 400)) < 1e-12 and v["n_symbols"] == 2


def test_run_upserts(monkeypatch):
    eng = create_engine("sqlite://")
    FinraDixDaily.__table__.create(eng)
    s = Session(eng)
    monkeypatch.setattr("trading_intel.market.breadth.sp500_symbols", lambda cv: ["AAPL", "MSFT"])
    d = date(2026, 11, 13)
    for _ in range(2):
        finra_dix.run(s, settings=object(), cv=_CV(), short=parse(TXT), day=d)
    rows = list(s.execute(select(FinraDixDaily)).scalars())
    assert len(rows) == 1 and rows[0].n_symbols == 2
