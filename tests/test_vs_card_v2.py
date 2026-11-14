"""VS card v2 (2026-11-13 overnight): lock/moved, trust, tells + grading, close pressure."""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import vs_card_v2 as V  # noqa: E402

P = {"spot": 7560.0, "flips": {"full": {"nearest": 7604.0}, "ex0": {"nearest": 7580.0}},
     "signed": {"ok": True, "flips": {"nearest": 7610.0}}, "pct": {"full": 22},
     "book": {"ts": "2026-11-13T17:15:00"}}


def test_levels_lock_and_trust():
    lv = {"balance": 7550.0, "up_test": 7600.0, "down_test": 7500.0}
    L = V.levels(P, lv)
    assert L["flip_full"] == 7604.0 and L["flip_signed"] == 7610.0
    lm = V.lock_moved(L, {"flip_full": 7602.0, "balance": 7525.0})
    assert lm["flip_full"] == "locked" and lm["balance"] == "moved +25" and lm["up_test"] == "new"
    t = V.trust(P, datetime(2026, 11, 14, 8, 50))
    assert t["word"] == "high"


def test_tells_and_grading(tmp_path):
    lv = {"balance": 7550.0, "up_test": 7600.0, "down_test": 7500.0}
    tl = V.tells(P, V.levels(P, lv))
    assert tl[0]["if"] == "close above 7,604"
    g = V.grade(tl, 7610.0)
    assert g[0]["fired"] and any(not x["fired"] for x in g)
    sc = V.update_score(tmp_path / "s.json", g)
    assert sc["flip"] == {"n": 1, "fired": 1}
    html = V.block_html(V.lock_moved(V.levels(P, lv), None), V.levels(P, lv), V.trust(P, datetime(2026, 11, 14)),
                        tl, g, sc, V.close_pressure(-3.0))
    assert "Tomorrow's tells" in html and "drift down" in html
