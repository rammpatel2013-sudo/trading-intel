"""Unit tests for v9_score.py (V9 toggle, created 2026-11-13 overnight). Run: python tests/test_v9_score.py (or pytest)."""
import json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import v9_score as V


def test_board_collecting_and_verdict():
    one = {"2026-11-16": [{"n": 10, "half": 2.0, "v9": 1.5}, {"n": 10, "half": 2.2, "v9": 1.6}]}
    b = V.board(one)
    assert b["days"] == 1 and b["v9_wins"] == 1 and b["verdict"].startswith("collecting")
    many = {"2026-11-%02d" % d: [{"n": 5, "half": 2.0, "v9": 1.0}] for d in range(1, 13)}
    assert V.board(many)["verdict"].startswith("VIX9D re-mark closer")
    worse = {"2026-11-%02d" % d: [{"n": 5, "half": 1.0, "v9": 2.0}] for d in range(1, 13)}
    assert V.board(worse)["verdict"].startswith("no clear winner")


def test_main_reads_readouts():
    with tempfile.TemporaryDirectory() as td:
        rows = [{"wkiv": {"n": 3, "half": 2.0, "v9": 1.9}}, {"wkiv": None}, {"x": 1}, {"wkiv": {"n": 0, "half": None, "v9": None}}]
        with open(os.path.join(td, "readouts_20261116.jsonl"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
        assert V.main(["--data", td, "--out", td]) == 0
        b = json.load(open(os.path.join(td, "scores", "v9_board.json"), encoding="utf-8"))
        assert b["days"] == 1 and b["per_day"]["2026-11-16"]["readouts"] == 1 and b["per_day"]["2026-11-16"]["winner"] == "v9"


if __name__ == "__main__":
    test_board_collecting_and_verdict(); test_main_reads_readouts(); print("ok")
