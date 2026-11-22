"""Convexity page + Risk-page card render (2026-11-22). Pure, no network."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import convexity_page as cp  # noqa: E402
import risk_page as rp  # noqa: E402


def _o():
    p = {"symbol": "AAA", "bucket": "cheap_up", "rank": 1, "score": 1.2, "spot": 100, "iv30": 0.3, "iv_rank": 0.1,
         "carry": -0.02, "ts_30_90": 0.9, "skew_rank": 0.5, "code": "2B", "wstage": 2, "mrs": 4.0, "move20": 0.08,
         "ret20": 0.05, "next_earn": None}
    return {"asof": "2026-11-20", "asof_stage": "2026-11-20", "built": "x", "counts": {"cheap_up": 1, "skipped": {}},
            "rules": {"earn_days": 25, "min_prem": 5e5, "top_n": 15},
            "picks": {"cheap_up": [p], "cheap_down": [], "rich_sell": [], "avoid": []},
            "board": [{"symbol": "TLT", "grp": "Bonds", "iv30": 0.14, "hv30": 0.1, "vrp": 0.04, "iv_rank": 0.7,
                       "ts_30_90": 0.98, "z20": -0.5, "quad": "sell premium", "iv_src": "convex_eod"}],
            "track": {"rows": [{"bucket": "cheap_up", "h": 5, "n": 3, "hit": 0.67, "base": 0.317, "rv_iv": 1.2}],
                      "lists": 4, "dates_scored": 3}}


def test_page(tmp_path):
    (tmp_path / "convexity.json").write_text(json.dumps(_o()), encoding="utf-8")
    cp.write(tmp_path)
    h = (tmp_path / "convexity.html").read_text(encoding="utf-8")
    for want in ("Cheap upside", "AAA", "Strategy map", "TLT", "sell premium", "+35 pts", "No names today."):
        assert want in h
    assert "<script" not in h
    assert cp.write(tmp_path / "missing") is None


def test_risk_card(tmp_path):
    (tmp_path / "convexity.json").write_text(json.dumps(_o()), encoding="utf-8")
    rp.write(tmp_path)
    h = (tmp_path / "risk.html").read_text(encoding="utf-8")
    assert "Where options look mispriced" in h and "convexity.html" in h and "rank 10%" in h
