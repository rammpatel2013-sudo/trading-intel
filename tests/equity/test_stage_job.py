"""stage_breadth job end to end on SQLite (2026-11-22): synthetic S&P members -> equity_stages + stagetab.json."""

import json

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from trading_intel.clients.equity_data import EquityDataSource
from trading_intel.equity import pipeline as pl
from trading_intel.memory.models_equity import EQUITY_TABLES, EquityStage
from trading_intel.scheduler.jobs import equity_weekly, stage_breadth

from .synth import END, World


@pytest.fixture()
def session():
    eng = create_engine("sqlite://")
    for m in EQUITY_TABLES:
        m.__table__.create(eng)
    with Session(eng) as s:
        yield s


def test_stage_job_writes_rows_and_page_data(session, tmp_path):
    world = World(n_sp500=40, n_ijh=10, days=420)
    src = EquityDataSource(world, per_sec=1e9)
    equity_weekly.run(session, src=src, day=END)
    pl.refresh_bars(session, src, pl.target_symbols(pl.store.load_members(session)), END)
    out = stage_breadth.run(session, src=src, day=END, out_dir=tmp_path, refresh=False)
    assert out["members"] >= 40 and out["rows"] > 10_000          # first run back-fills every day
    o = json.loads((tmp_path / "stagetab.json").read_text(encoding="utf-8"))
    assert o["asof"] == END.isoformat() and [c["id"] for c in o["cards"]][:2] == ["STAGE2", "STAGE4"]
    assert {"WS2", "WS4"} <= {c["id"] for c in o["cards"]} and o["weekly"]["mix"]["dates"]
    n1 = session.execute(select(func.count()).select_from(EquityStage)).scalar()
    again = stage_breadth.run(session, src=src, day=END, out_dir=tmp_path, refresh=False)
    assert again["rows"] < out["rows"]                             # later runs redo only the last sessions
    assert session.execute(select(func.count()).select_from(EquityStage)).scalar() == n1   # idempotent
