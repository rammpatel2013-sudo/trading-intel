"""terrain (2026-11-19): price terrain for the test cockpit's Flow panel F3 - minutes at price per bin over the last 3
sessions, HVN / LVN / MID / NEW (rule tr-v0)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "cvxmap"))
import terrain


def _log(path: Path, day: str, xs: list[float], sym: str = "SPX", start: int = 9 * 60 + 30) -> None:
    with path.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"t": "09:10", "d": day, "s": sym, "x": 1.0}) + "\n")   # pre-open: ignored
        for i, x in enumerate(xs):
            m = start + i
            fh.write(json.dumps({"t": f"{m // 60:02d}:{m % 60:02d}", "d": day, "s": sym, "x": x}) + "\n")
        fh.write("not json\n")


def test_bins_and_percentiles():
    assert terrain.bin_low(7209.22, 5.0) == 7205.0 and terrain.bin_low(571.37, 0.5) == 571.0
    assert terrain.pct([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.7) == 7 and terrain.pct([4], 0.3) == 4


def test_build_last_three_sessions_and_classify(tmp_path):
    _log(tmp_path / "flow_20261116.ndjson", "2026-11-16", [7000.0] * 50)         # 4th day back: not used
    _log(tmp_path / "flow_20261117.ndjson", "2026-11-17", [7100.0] * 30 + [7111.0] * 2)
    _log(tmp_path / "flow_20261118.ndjson", "2026-11-18", [7100.0] * 20 + [7120.0] * 10)
    _log(tmp_path / "flow_20261119.ndjson", "2026-11-19", [7102.0] * 10 + [7130.0] * 5)
    _log(tmp_path / "flow_20261120.ndjson", "2026-11-20", [7500.0] * 99)         # today: not used
    j = terrain.build(str(tmp_path), "20261120")
    assert j["from"] == ["20261117", "20261118", "20261119"]
    s = j["syms"]["SPX"]
    assert s["m"] == {"7100.0": 60, "7110.0": 2, "7120.0": 10, "7130.0": 5}
    m = {float(k): v for k, v in s["m"].items()}
    assert terrain.classify(m, 7101.0, 5.0, s["hvn"], s["lvn"]) == "HVN"
    assert terrain.classify(m, 7112.0, 5.0, s["hvn"], s["lvn"]) == "LVN"
    assert terrain.classify(m, 7300.0, 5.0, s["hvn"], s["lvn"]) == "NEW"
    p = terrain.write(str(tmp_path), "20261120")
    assert json.loads(Path(p).read_text())["rule"] == "tr-v0"


def test_rth_only_and_one_count_per_minute():
    lines = [json.dumps({"t": t, "d": "2026-11-19", "s": "SPY", "x": 600.2}) for t in ("09:29", "09:30", "09:30", "15:59", "16:00")]
    assert terrain.minutes_at_price(lines)["SPY"] == {600.0: 2}
