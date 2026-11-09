"""Smoke test for scripts/tenor_gamma_report.py on a SQLite fixture (2026-11-09)."""

from __future__ import annotations

import json
from datetime import date, datetime

import numpy as np
import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.clients.telegram import MUTED_JOBS
from trading_intel.market import tenor_read
from trading_intel.memory.models import GreeksSnapshot, OiChainEod
from trading_intel.scheduler.jobs._script_report import load_script


def _session() -> Session:
    eng = create_engine("sqlite://")
    OiChainEod.__table__.create(eng)
    GreeksSnapshot.__table__.create(eng)
    s = Session(eng)
    for j, d in enumerate((datetime(2026, 11, 5), datetime(2026, 11, 6))):
        s.add(GreeksSnapshot(symbol="SPX", ts=d.replace(hour=17), spot=7370.0 + j, source="convex"))
        for exp in (date(2026, 11, 9), date(2026, 11, 13), date(2026, 11, 20), date(2026, 12, 18)):
            for k in (7200.0, 7400.0, 7600.0):
                s.add(
                    OiChainEod(
                        symbol="SPX",
                        ts=d,
                        expiry=exp,
                        strike=k,
                        cp="C",
                        iv=0.17,
                        oi=1000,
                        source="convex_eod",
                    )
                )
                s.add(
                    OiChainEod(
                        symbol="SPX",
                        ts=d,
                        expiry=exp,
                        strike=k,
                        cp="P",
                        iv=0.22,
                        oi=2500,
                        source="convex_eod",
                    )
                )
    s.commit()
    return s


def test_job_not_muted():
    assert "tenor_gamma_report" not in MUTED_JOBS


def test_build_render_and_caption(monkeypatch, tmp_path):
    mod = load_script("tenor_gamma_report")
    from trading_intel.market import factor_cards as fc
    from trading_intel.market import factor_prices as fp

    idx = pd.bdate_range("2012-01-02", periods=2000)
    rng = np.random.default_rng(5)
    px = pd.DataFrame(
        {s: 100 * np.exp(np.cumsum(rng.normal(0, 0.01, len(idx)))) for s in fc.SYMBOLS}, idx
    )
    monkeypatch.setattr(
        fp, "load_closes", lambda syms, settings=None: (px, {s: "fmp" for s in syms})
    )

    obj = mod.build_obj(_session(), "SPX", backfill=True, hist_root=tmp_path)
    assert obj["found"] and obj["asof"] == "2026-11-06"
    assert [r["date"] for r in obj["history"]] == ["2026-11-05", "2026-11-06"]
    assert len(obj["factors"]["cards"]) == 6
    assert obj["read"][-1].lower().startswith("positioning read, not a trade signal")
    out = mod.write_json(obj, tmp_path / "tenor.json")
    back = json.loads(out.read_text())
    assert back["gamma"]["total"]["at_spot"] == obj["gamma"]["total"]["at_spot"]
    html = mod.render_html(obj)
    assert "<svg" in html and "not a trade signal" in html
    cap = tenor_read.caption(obj)
    assert len(cap) <= 1024 and "not a trade signal" in cap


def test_push_sends_one_message(monkeypatch, tmp_path):
    mod = load_script("tenor_gamma_report")
    sent = []

    class TG:
        def __init__(self, _s):
            pass

        def send_photo(self, p, caption=""):
            sent.append(("photo", caption))
            return True

        def send_document(self, p, caption=""):
            sent.append(("doc", caption))
            return True

    import trading_intel.clients.telegram as tg

    monkeypatch.setattr(tg, "TelegramClient", TG)
    monkeypatch.setattr(mod, "rasterize", lambda p: None)
    h = tmp_path / "x.html"
    h.write_text("<html></html>")
    assert mod.push({"symbol": "SPX", "asof": "2026-11-06"}, h, settings=None) is True
    assert len(sent) == 1 and sent[0][0] == "doc"


def test_no_book_still_writes(tmp_path):
    mod = load_script("tenor_gamma_report")
    eng = create_engine("sqlite://")
    OiChainEod.__table__.create(eng)
    GreeksSnapshot.__table__.create(eng)
    obj = mod.build_obj(Session(eng), "SPX", factors=False, hist_root=tmp_path)
    assert obj["found"] is False and obj["history"] == []
    assert "No stored chain book" in obj["read"][0]
    assert "<html" in mod.render_html(obj)
