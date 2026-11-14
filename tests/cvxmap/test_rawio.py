"""rawio.read_lines (2026-11-14, O2 / code audit B1): the live writer reads only the new part of the day's raw file.
Synthetic multi-member gzip files written the way the collector writes them (gzip.open(path, "at"), one json line each)."""
import gzip
import importlib.util
import json
from pathlib import Path

_P = Path(__file__).resolve().parents[2] / "scripts" / "cvxmap" / "rawio.py"
_S = importlib.util.spec_from_file_location("rawio_under_test", _P)
rawio = importlib.util.module_from_spec(_S)
_S.loader.exec_module(rawio)


def _write(path, n, start=0):
    for k in range(start, start + n):
        with gzip.open(path, "at", encoding="utf-8") as fh:
            fh.write(json.dumps({"k": k, "pad": "x" * (k * 131 % 5000)}) + "\n")


def _gz_lines(path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return [ln.rstrip("\n") for ln in fh]


def test_from_zero_matches_gzip(tmp_path):
    p = tmp_path / "raw.ndjson.gz"
    _write(p, 40)
    lines, off, line0 = rawio.read_lines(str(p), 0, 0)
    assert [t for _, t in lines] == _gz_lines(p)
    assert [i for i, _ in lines] == list(range(40))
    assert off == p.stat().st_size and line0 == 40


def test_incremental_equals_one_read(tmp_path):
    p = tmp_path / "raw.ndjson.gz"
    got, off, line0 = [], 0, 0
    for step in range(6):                       # the collector appends, the writer reads what is new each pass
        _write(p, 7, start=step * 7)
        new, off, line0 = rawio.read_lines(str(p), off, line0)
        got += new
    assert got == list(enumerate(_gz_lines(p)))
    assert rawio.read_lines(str(p), off, line0) == ([], off, line0)   # nothing new = nothing read


def test_half_written_member_is_left_for_next_pass(tmp_path):
    p = tmp_path / "raw.ndjson.gz"
    _write(p, 10)
    full = p.read_bytes()
    _write(p, 1, start=10)
    whole = p.read_bytes()
    p.write_bytes(whole[: len(full) + 25])       # the 11th member is only partly on disk
    lines, off, line0 = rawio.read_lines(str(p), 0, 0)
    assert len(lines) == 10 and off == len(full) and line0 == 10
    p.write_bytes(whole)                         # the collector finishes the write
    more, off2, line2 = rawio.read_lines(str(p), off, line0)
    assert [i for i, _ in more] == [10] and off2 == len(whole) and line2 == 11


def test_member_without_line_end_waits_for_the_rest(tmp_path):
    p = tmp_path / "raw.ndjson.gz"
    with gzip.open(p, "at", encoding="utf-8") as fh:
        fh.write('{"a":1}\n{"b":')               # a line split across two members
    lines, off, line0 = rawio.read_lines(str(p), 0, 0)
    assert lines == [] and off == 0 and line0 == 0
    with gzip.open(p, "at", encoding="utf-8") as fh:
        fh.write('2}\n')
    lines, off, line0 = rawio.read_lines(str(p), 0, 0)
    assert [t for _, t in lines] == ['{"a":1}', '{"b":2}'] and off == p.stat().st_size and line0 == 2


def test_missing_file(tmp_path):
    assert rawio.read_lines(str(tmp_path / "nope.gz"), 5, 3) == ([], 5, 3)


def test_crlf_lines_read_like_gzip(tmp_path):
    """Windows text mode writes CRLF (the laptop test run); the NAS collector writes LF. Both read as gzip "rt" gives."""
    for nl in ("\n", "\r\n"):
        p = tmp_path / ("raw_%d.ndjson.gz" % len(nl))
        for k in range(5):
            with gzip.open(p, "at", encoding="utf-8", newline=nl) as fh:
                fh.write(json.dumps({"k": k}) + "\n")
        lines, off, line0 = rawio.read_lines(str(p), 0, 0)
        assert [t for _, t in lines] == _gz_lines(p) == [json.dumps({"k": k}) for k in range(5)]
        assert off == p.stat().st_size and line0 == 5
