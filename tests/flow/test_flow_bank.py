"""Flow bank (2026-11-19): parsers + the job end to end on SQLite with fake vendors."""

import csv
import gzip
import io
import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from trading_intel.flow import bank
from trading_intel.memory.models_flow import (
    FLOW_TABLES,
    FLOWCHART_COLS,
    UND_FIELDS,
    Bars1m,
    FlowChart15s,
    FlowUndDaily,
    OdFlowDaily,
)
from trading_intel.scheduler.jobs import flow_bank

DAY = date(2026, 11, 19)
ET = ZoneInfo("America/New_York")


@pytest.fixture()
def session():
    eng = create_engine("sqlite://")
    for t in FLOW_TABLES:
        t.__table__.create(eng)
    with Session(eng) as s:
        yield s


def _und_resp(split: float = 100.0) -> dict:
    rows = []
    for sym in ("SPY", "QQQ"):
        vals = [split if f in bank.SPLIT else 1.5 for f in UND_FIELDS]
        rows.append([sym, *vals])
    rows.append(["", *([0.0] * len(UND_FIELDS))])  # no symbol -> dropped
    rows.append(["BAD"])  # short row -> dropped
    return {"data": [rows]}


def _epoch(h: int, m: int, s: int = 0) -> float:
    return datetime(2026, 11, 19, h, m, s, tzinfo=ET).timestamp()


def _fc_resp(sym: str) -> dict:
    head = ["symbol", "time", *FLOWCHART_COLS]
    rows = [[sym, _epoch(9, 30, 0) + 15 * i, *[float(i)] * len(FLOWCHART_COLS)] for i in range(4)]
    rows[1][4] = None  # prop1 null
    return {"data": [head, rows]}


class FakeApp:
    def __init__(self, split: float = 100.0, fail: set | None = None):
        self.split, self.fail, self.calls = split, fail or set(), []

    def und_flow(self, fields):
        self.calls.append("und")
        assert list(fields) == list(UND_FIELDS)
        return _und_resp(self.split)

    def und_get(self, symbols, fields):
        self.calls.append("und_get")
        assert "SPX" in symbols
        live = [["SPX", *[self.split if f in bank.SPLIT else 2.0 for f in UND_FIELDS]]]
        dead = [["XSP", *([0.0] * len(UND_FIELDS))]]
        return {"data": [live + dead]}

    def flowchart(self, sym, d=None, cols=None):
        self.calls.append(sym)
        if sym in self.fail:
            raise RuntimeError("boom")
        return _fc_resp(sym)


class FakeCV:
    def fmp(self, endpoint, params):
        assert endpoint == "historical-chart/1min"
        return [
            {"date": "2026-11-19 15:59:00", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10},
            {"date": "2026-11-18 15:59:00", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10},
        ]


def _od_folder(tmp_path):
    folder = tmp_path / "reports" / "optionsdata" / "20261119"
    folder.mkdir(parents=True)
    head = ["record", "level", "expiration", "right", "category", "side", "premium", "volume", "count"]

    def w(name, rows):
        buf = io.StringIO()
        cw = csv.writer(buf)
        cw.writerow(head)
        cw.writerows(rows)
        with gzip.open(folder / name, "wt", encoding="utf-8", newline="") as f:
            f.write(buf.getvalue())

    w("SPY_underlying.csv.gz", [
        ["flow", "underlying_right", "", "call", "eth", "buy", "100", "5", "2"],
        ["term", "", "2026-11-20", "", "", "", "", "", ""],
    ])
    w("SPY.csv.gz", [
        ["flow", "expiration_right", "2026-11-20", "put", "regular", "undecided", "50", "3", "1"],
        ["flow", "contract", "2026-11-20", "put", "regular", "sell", "9", "1", "1"],  # not kept
    ])
    return folder


def test_parse_und_and_split():
    rows = bank.parse_und(_und_resp())
    assert [r["symbol"] for r in rows] == ["SPY", "QQQ"]
    assert rows[0]["volm_call_buy"] == 100.0 and rows[0]["flowratio"] == 1.5
    assert bank.split_ok(rows) and not bank.split_ok(bank.parse_und(_und_resp(0.0)))


def test_parse_flowchart_eastern_naive():
    rows = bank.parse_flowchart(_fc_resp("SPX"))
    assert len(rows) == 4
    assert rows[0]["ts"] == datetime(2026, 11, 19, 9, 30) and rows[0]["ts"].tzinfo is None
    assert rows[1]["prop1"] is None and rows[3]["flownet"] == 3.0


def test_parse_od_flow_and_fmp():
    rows = bank.parse_od_flow(DAY, "spy", [
        {"record": "flow", "level": "underlying_right", "right": "call", "category": "eth",
         "side": "buy", "premium": "1", "volume": "2", "count": "3"},
        {"record": "flow", "level": "contract", "right": "call", "category": "eth", "side": "buy"},
    ])
    assert rows == [{"date": DAY, "symbol": "SPY", "expiration": None, "right": "call",
                     "category": "eth", "side": "buy", "premium_usd": 1.0, "volume": 2.0, "count": 3.0}]
    bars = bank.parse_fmp_1m("spy", [{"date": "2026-11-19 09:30:00", "close": "1.5"}, {"date": "x"}])
    assert bars[0]["ts"] == datetime(2026, 11, 19, 9, 30) and bars[0]["close"] == 1.5 and len(bars) == 1


def test_job_end_to_end_and_rerun(session, tmp_path, monkeypatch):
    monkeypatch.setattr(flow_bank, "OD_DIR", _od_folder(tmp_path).parent)
    app = FakeApp()
    now = datetime(2026, 11, 19, 17, 25)
    out = flow_bank.run(session, object(), app=app, cvforge=FakeCV(), day=DAY, now=now,
                        force=True, sleep=lambda s: None, steps=flow_bank.STEPS)
    assert out["errors"] == [] and out["und"] == {"rows": 4, "index_zero": ["XSP"]}
    n = lambda m: session.scalar(select(func.count()).select_from(m))  # noqa: E731
    assert n(FlowUndDaily) == 4
    assert n(FlowChart15s) == 4 * len(flow_bank.FLOWCHART_SYMBOLS)
    assert n(OdFlowDaily) == 2
    assert n(Bars1m) == len(flow_bank.BAR_SYMBOLS)  # the 11/18 bar is dropped
    flow_bank.run(session, object(), app=FakeApp(), cvforge=FakeCV(), day=DAY, now=now,
                  force=True, sleep=lambda s: None, steps=flow_bank.STEPS)  # re-run = same rows
    assert n(FlowUndDaily) == 4 and n(FlowChart15s) == 4 * len(flow_bank.FLOWCHART_SYMBOLS)


def test_job_reports_failures_but_keeps_other_steps(session, tmp_path, monkeypatch):
    monkeypatch.setattr(flow_bank, "OD_DIR", tmp_path / "missing")
    with pytest.raises(RuntimeError, match="od_flow"):
        flow_bank.run(session, object(), app=FakeApp(split=0.0, fail={"VIX"}), cvforge=FakeCV(),
                      day=DAY, now=datetime(2026, 11, 19, 17, 25), force=True, sleep=lambda s: None,
                      steps=flow_bank.STEPS)
    assert session.scalar(select(func.count()).select_from(FlowUndDaily)) == 0  # split all 0 -> not saved
    assert session.scalar(select(func.count()).select_from(Bars1m)) > 0  # bars step still ran


def test_job_skips_non_session(session):
    out = flow_bank.run(session, object(), app=FakeApp(), cvforge=FakeCV(), day=date(2026, 11, 21))
    assert out == {"skipped": "not_a_trading_session"}


def test_import_dir(session, tmp_path):
    resp = _und_resp()
    resp["params"] = ["symbol", *UND_FIELDS]
    with gzip.open(tmp_path / "und_20261119.json.gz", "wt") as f:
        json.dump(resp, f)
    with gzip.open(tmp_path / "flowchart_SPX_20261119.json.gz", "wt") as f:
        json.dump(_fc_resp("SPX"), f)
    out = flow_bank.import_dir(session, tmp_path, DAY)
    assert out == {"und": 2, "SPX": 4}


def test_od_only_step_needs_no_convex(session, tmp_path, monkeypatch):
    monkeypatch.setattr(flow_bank, "OD_DIR", _od_folder(tmp_path).parent)
    out = flow_bank.run(session, object(), app=None, cvforge=None, day=DAY,
                        now=datetime(2026, 11, 19, 17, 25), force=True, steps=("od_flow",))
    assert out["od_flow"] == {"rows": 2} and out["errors"] == []


def test_merge_und_keeps_live_rows():
    a = [{"symbol": "SPX", "value": 5.0}]
    b = [{"symbol": "SPX", "value": 0.0}, {"symbol": "XSP", "value": 0.0}]
    assert {r["symbol"]: r["value"] for r in bank.merge_und(a, b)} == {"SPX": 5.0, "XSP": 0.0}
