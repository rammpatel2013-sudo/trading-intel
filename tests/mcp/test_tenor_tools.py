"""MCP tenor tools + the tenor_data DB layer on SQLite (2026-11-09)."""

from __future__ import annotations

import json
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.market import tenor_data as td
from trading_intel.mcp import tenor_tools as tt
from trading_intel.memory.models import GreeksSnapshot, OiChainEod

DAYS = (
    datetime(2026, 10, 30),
    datetime(2026, 11, 2),
    datetime(2026, 11, 3),
    datetime(2026, 11, 4),
    datetime(2026, 11, 5),
    datetime(2026, 11, 6),
)


def _session() -> Session:
    eng = create_engine("sqlite://")
    OiChainEod.__table__.create(eng)
    GreeksSnapshot.__table__.create(eng)
    s = Session(eng)
    for j, ts in enumerate(DAYS):
        spot = 7400.0 - 10 * j
        s.add(GreeksSnapshot(symbol="SPX", ts=ts.replace(hour=17), spot=spot, source="convex"))
        for exp in (date(2026, 11, 9), date(2026, 11, 13), date(2026, 11, 20), date(2026, 12, 18)):
            for k in (7200.0, 7400.0, 7600.0):
                for cp, iv, oi in (("C", 0.17, 1000 + 100 * j), ("P", 0.22, 2500)):
                    s.add(
                        OiChainEod(
                            symbol="SPX",
                            ts=ts,
                            expiry=exp,
                            strike=k,
                            cp=cp,
                            iv=iv,
                            oi=oi,
                            dte=(exp - ts.date()).days,
                            source="convex_am_set" if exp == date(2026, 11, 20) else "convex_eod",
                        )
                    )
        # junk the reader must ignore: other symbol, other source
        s.add(
            OiChainEod(
                symbol="AAPL",
                ts=ts,
                expiry=date(2026, 11, 13),
                strike=200.0,
                cp="C",
                iv=0.3,
                oi=10**6,
                source="convex_eod",
            )
        )
        s.add(
            OiChainEod(
                symbol="SPX",
                ts=ts,
                expiry=date(2026, 11, 13),
                strike=7400.0,
                cp="C",
                iv=0.3,
                oi=10**6,
                source="cvforge",
            )
        )
    s.commit()
    return s


def test_compute_day_reads_one_book_incl_am_settled():
    s = _session()
    out = td.compute_day(s, "SPX")
    assert out["found"] and out["as_of"] == "2026-11-06" and out["spot"] == 7350.0
    g = out["gamma"]
    assert g["buckets"]["1w_1m"]["expiries"] == ["2026-11-20"]  # convex_am_set kept
    assert g["n_contracts"] == 24  # junk rows excluded
    old = td.compute_day(s, "SPX", date(2026, 11, 3))
    assert old["as_of"] == "2026-11-03" and old["spot"] == 7380.0


def test_history_backfill_idempotent(tmp_path):
    s = _session()
    rows = td.backfill(s, "SPX", tmp_path)
    assert [r["date"] for r in rows] == [d.date().isoformat() for d in DAYS]
    td.backfill(s, "SPX", tmp_path)
    td.write_history("SPX", rows[-1:], tmp_path)
    again = td.read_history("SPX", tmp_path)
    assert len(again) == len(DAYS)
    r = again[-1]
    assert set(f"g_{k}" for k in ("1d", "2d_1w", "1w_1m", "gt_1m")) <= set(r)
    ch = td.change_vs(again, 5)
    assert ch["from"] == "2026-10-30" and ch["to"] == "2026-11-06"
    assert td.change_vs(again[:3], 5) is None


def test_tools_compute_when_no_precomputed(tmp_path):
    s = _session()
    missing = tmp_path / "nope.json"
    g = tt.gamma_by_tenor(s, "spx", path=missing)
    assert g["found"] and g["source"] == "computed" and g["note"] == tt.DISCLAIMER
    v = tt.vega_by_tenor(s, "SPX", date(2026, 11, 4), path=missing)
    assert v["as_of"] == "2026-11-04" and "short_all" in v["vega"]["total"]
    h = tt.tenor_history(s, "SPX", days=3, root=tmp_path)
    assert h["n"] == 3 and h["source"].startswith("computed from oi_chain_eod")
    assert not td.hist_path("SPX", tmp_path).exists()  # the MCP tool never writes
    td.backfill(s, "SPX", tmp_path)
    h2 = tt.tenor_history(s, "SPX", days=30, root=tmp_path)
    assert h2["source"] == "history file" and h2["n"] == len(DAYS)


def test_tools_prefer_precomputed(tmp_path):
    p = tmp_path / "tenor.json"
    p.write_text(
        json.dumps(
            {
                "found": True,
                "symbol": "SPX",
                "asof": "2026-11-06",
                "spot": 7376.86,
                "gamma": {"total": {"at_spot": -82.2}},
                "vega": {"total": {"naive": -1.0}},
            }
        )
    )
    g = tt.gamma_by_tenor(None, "SPX", path=p)
    assert g["source"] == "precomputed" and g["gamma"]["total"]["at_spot"] == -82.2
    # a different day falls through to the DB (none here)
    assert tt.gamma_by_tenor(None, "SPX", date(2026, 11, 5), path=p)["found"] is False
    assert tt.vega_by_tenor(None, "SPX", path=p)["source"] == "precomputed"


def test_factor_cards_precomputed(tmp_path):
    p = tmp_path / "f.json"
    p.write_text(json.dumps({"cards": [{"key": "beta", "found": True}], "note": "x"}))
    out = tt.factor_cards(path=p)
    assert out["found"] and out["source"] == "precomputed"


def test_factor_cards_compute_path(monkeypatch, tmp_path):
    from trading_intel.market import factor_cards as fc
    from trading_intel.market import factor_prices as fp

    idx = pd.bdate_range("2014-01-01", periods=1500)
    rng = np.random.default_rng(1)
    px = pd.DataFrame(
        {s: 100 * np.exp(np.cumsum(rng.normal(0, 0.01, len(idx)))) for s in fc.SYMBOLS}, idx
    )
    monkeypatch.setattr(
        fp, "load_closes", lambda syms, settings=None: (px, {s: "fmp" for s in syms})
    )
    out = tt.factor_cards(path=tmp_path / "missing.json")
    assert out["found"] and out["source"] == "computed" and len(out["cards"]) == 6


def test_server_registers_tenor_tools():
    pytest.importorskip("fastmcp")
    import asyncio

    from sqlalchemy.orm import sessionmaker

    from trading_intel.config import Settings
    from trading_intel.mcp.server import build_server

    class _LLM:
        pass

    mcp = build_server(
        Settings(), llm=_LLM(), session_factory=sessionmaker(bind=create_engine("sqlite://"))
    )
    lister = getattr(mcp, "list_tools", None) or mcp.get_tools
    tools = asyncio.run(lister())
    names = {t.name for t in tools} if not isinstance(tools, dict) else set(tools)
    assert {
        "get_gamma_by_tenor",
        "get_vega_by_tenor",
        "get_tenor_history",
        "get_factor_cards",
    } <= names
