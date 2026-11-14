"""flow_tail.json (2026-11-14, O3 / code audit A1): the page loads the minute log whole once, then only the new rows from
the tail cvx_live.py writes after each append. These tests replay the page's merge rule in Python on synthetic logs.
Logs are written LF and read as bytes so the tests give the same answer on Windows (fixed 2026-11-14 after the
o3 ship tests failed on the laptop: text mode there wrote CRLF and read_text() dropped the \r)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "cvxmap"))
import cvx_live as L  # noqa: E402


def _row(t, s, pad=300):
    return json.dumps({"t": t, "s": s, "x": 1.0, "k": [1], "cv": [0], "pad": "x" * pad}, separators=(",", ":")) + "\n"


def _append(fp, rows):
    with open(fp, "a", encoding="utf-8", newline="\n") as fh:   # LF like the NAS writer (Windows text mode would add \r)
        fh.write("".join(rows))


def _tail(tmp_path):
    return json.load(open(tmp_path / "flow_tail.json", encoding="utf-8"))


def _page_merge(have, tail):
    """the page's rule: None = load the whole log again, else the text to add"""
    size, gen, text = have
    if tail["gen"] != gen or tail["size"] < size or tail["from"] > size:
        return None
    off = size - tail["from"]
    if off > 0 and tail["text"][off - 1] != "\n":
        return None
    return tail["text"][off:]


def test_tail_rows_and_offsets(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "DATA", str(tmp_path))
    fp = tmp_path / "flow_20261116.ndjson"
    state = {}
    rows = [_row("09:%02d" % m, s) for m in range(30, 59) for s in ("SPX", "SPY", "QQQ")]
    _append(fp, rows)
    L.write_flow_tail(str(fp), "20261116", state)
    t = _tail(tmp_path)
    data = fp.read_bytes()
    assert t["size"] == len(data) == state["flow_size"]
    assert data[t["from"]:].decode() == t["text"] and t["text"].count("\n") == L.TAIL_LINES
    assert t["day"] == "20261116" and t["gen"] == 0


def test_page_merge_equals_whole_load(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "DATA", str(tmp_path))
    fp = tmp_path / "flow_20261116.ndjson"
    state = {}
    _append(fp, [_row("09:30", s) for s in ("SPX", "SPY", "QQQ")])
    L._flow_gen_check(str(fp), state)
    L.write_flow_tail(str(fp), "20261116", state)
    have_text = fp.read_bytes().decode("utf-8")      # the page's first whole load (bytes as served, no newline translation)
    have = (len(have_text.encode()), _tail(tmp_path)["gen"], None)
    for m in range(31, 50):                          # the writer appends one minute per pass, the page polls each time
        L._flow_gen_check(str(fp), state)
        _append(fp, [_row("09:%02d" % m, s) for s in ("SPX", "SPY", "QQQ")])
        L.write_flow_tail(str(fp), "20261116", state)
        add = _page_merge(have, _tail(tmp_path))
        assert add is not None
        have_text += add
        have = (len(have_text.encode()), have[1], None)
    assert have_text == fp.read_bytes().decode("utf-8")


def test_rewrite_bumps_gen_and_page_reloads(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "DATA", str(tmp_path))
    fp = tmp_path / "flow_20261116.ndjson"
    state = {}
    _append(fp, [_row("09:31", s) for s in ("SPX", "SPY", "QQQ")])
    L._flow_gen_check(str(fp), state)
    L.write_flow_tail(str(fp), "20261116", state)
    page = (fp.stat().st_size, _tail(tmp_path)["gen"], None)
    # backfill_merge rewrites the log with a missing 09:30 minute in front
    fp.write_bytes("".join(_row("09:30", s) for s in ("SPX", "SPY", "QQQ")).encode() + fp.read_bytes())
    L._flow_gen_check(str(fp), state)
    _append(fp, [_row("09:32", s) for s in ("SPX", "SPY", "QQQ")])
    L.write_flow_tail(str(fp), "20261116", state)
    t = _tail(tmp_path)
    assert t["gen"] == 1 and _page_merge(page, t) is None   # -> whole reload


def test_tail_never_ends_mid_line(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "DATA", str(tmp_path))
    fp = tmp_path / "flow_20261116.ndjson"
    _append(fp, [_row("09:30", "SPX"), '{"t":"09:31","s":"SP'])
    L.write_flow_tail(str(fp), "20261116", {})
    t = _tail(tmp_path)
    assert t["text"].endswith("\n") and t["size"] == len(_row("09:30", "SPX"))
