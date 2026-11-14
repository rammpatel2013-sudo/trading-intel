"""rail_score.py (2026-11-13): rails reaction vs transition + TEST short-strike marker sm-v0. Synthetic data, made-up numbers."""
import importlib.util
import random
from pathlib import Path

_D = Path(__file__).resolve().parents[2] / "scripts" / "cvxmap"


def _load(name, file):
    s = importlib.util.spec_from_file_location(name, _D / file)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


rs = _load("rail_score_under_test", "rail_score.py")
p1 = _load("p1score_for_rail_test", "p1score.py")


def _path(prices, t0=571):
    return [(t0 + i, float(x)) for i, x in enumerate(prices)]


def test_score_level_same_as_p1score():
    rnd = random.Random(7)
    for _ in range(300):
        x, pr = 5000.0, []
        for _ in range(120):
            x += rnd.choice([-3, -2, -1, 0, 1, 2, 3])
            pr.append(x)
        L = 5000 + rnd.choice([-30, -15, -5, 0, 5, 15, 30])
        a = rs.score_level(_path(pr), L)
        a.pop("_i", None)
        assert a == p1.score_level(_path(pr), L)


def _board(S=5000.0, pos=()):
    """put-heavy (negative) board from S-100 to S+100, call-heavy (positive) at the strikes in pos."""
    rows = []
    for k in range(int(S) - 100, int(S) + 105, 5):
        c, p = (900, 100) if k in pos else (100, 400)
        rows.append((float(k), c, p, 0.15, 0.16))
    return rows


def test_marks_call_zone_and_short_behind_it():
    m = rs.marks(_board(pos=(5035, 5040, 4960)), 5000.0, 20.0, 600)
    assert m["rule"] == "sm-v0"
    assert m["call"]["rail"] == 5030.0 and m["call"]["zone"] == [5035.0, 5040.0] and m["call"]["short"] == 5045.0
    assert m["put"]["rail"] == 4965.0 and m["put"]["zone"] == [4960.0, 4960.0] and m["put"]["short"] == 4955.0
    assert m["call"]["x"] == 2.25


def test_marks_inside_the_rail_does_not_count():
    m = rs.marks(_board(pos=(5020, 5025)), 5000.0, 20.0, 600)   # positive strikes inside the 1.5x rail (5030) only
    assert m["call"]["short"] is None and "transition" in m["call"]["why"]


def test_marks_put_side_starts_at_1_75x():
    m = rs.marks(_board(pos=(4970,)), 5000.0, 20.0, 600)        # 4970 = 1.5x down, inside the 1.75x put rail (4965)
    assert m["put"]["short"] is None


def test_marks_beyond_3x_does_not_count():
    m = rs.marks(_board(pos=(5065,)), 5000.0, 20.0, 600)        # 3x = 5060
    assert m["call"]["short"] is None


def test_marks_from_flow_row():
    rows = _board(pos=(5035, 5040))
    row = {"x": 5000.0, "k": [r[0] for r in rows], "ci": [r[3] for r in rows], "pi": [r[4] for r in rows],
           "cm": [12.0 if r[0] == 5000 else 1.0 for r in rows], "pm": [8.0 if r[0] == 5000 else 1.0 for r in rows]}
    oi = {"%.1f" % r[0]: [r[1], r[2]] for r in rows}
    m = rs.marks_from_flow(row, oi, rs.mm("09:45"))
    assert m["straddle"] == 20.0 and m["call"]["short"] == 5045.0 and m["t"] == "09:45"


def test_mult_names():
    assert [rs.mult_of(n) for n in ("STR", "Straddle", "STR 1.5", "STR 2", "2x", "1σ", "OVN EM", "Weekly EM", "½σ",
                                     "Expected range", "1.5× straddle", "2× RAILS")] == \
        ["1x", "1x", "1.5x", "2x", "2x", "1.25x", "overnight", "weekly", "0.5σ", "1x", "1.5x", "2x"]


def test_score_day_lines_sides_and_past():
    rows = _board(pos=(5035, 5040))
    flow = {"09:45": {"x": 5000.0, "k": [r[0] for r in rows], "ci": [r[3] for r in rows], "pi": [r[4] for r in rows],
                      "cm": [12.0 if r[0] == 5000 else 1.0 for r in rows], "pm": [8.0 if r[0] == 5000 else 1.0 for r in rows]}}
    oi = {"%.1f" % r[0]: [r[1], r[2]] for r in rows}
    own = {"rails": [{"name": "STR", "lo": 4980.0, "hi": 5020.0}, {"name": "1σ", "lo": 4975.0, "hi": 5025.0}]}
    doc = {"rails": [{"name": "STR", "lo": 4980.0, "hi": 5020.0}, {"name": "Straddle", "lo": 4980.0, "hi": 5020.0},
                     {"name": "STR 1.5", "lo": 4970.0, "hi": 5030.0}]}
    pr = [5000 + i for i in range(0, 30)] + [5029 - i for i in range(0, 60)]   # up to 5029 then down to 4970
    d = rs.score_day(_path(pr), flow, oi, doc, own)
    L = {(r["src"], r["mult"], r["side"]): r for r in d["lines"]}
    assert sum(1 for r in d["lines"] if r["src"] == "Doc" and r["mult"] == "1x") == 2      # STR + Straddle counted once per side
    assert L[("Doc", "1x", "call")]["result"] in ("broke", "held")
    assert L[("Ours", "1.5x", "call")]["level"] == 5030.0 and L[("Ours", "2x", "put")]["level"] == 4960.0
    assert L[("Marker", "short", "call")]["level"] == 5045.0 and L[("Marker", "short", "call")]["result"] == "untouched"
    up = L[("Doc", "1x", "call")]
    assert up["past"] == 9.0 and up["past_x"] == 0.45 and up["close_beyond"] is False


def test_running_and_html():
    d = {"rails": {"lines": [{"src": "Ours", "mult": "1x", "side": "put", "result": "held", "past_x": 0.2, "close_beyond": False},
                             {"src": "Ours", "mult": "1x", "side": "put", "result": "broke", "past_x": 1.0, "close_beyond": True},
                             {"src": "Marker", "mult": "short", "side": "call", "result": "untouched", "past_x": None, "close_beyond": False}]}}
    R = rs.running([("2026-11-16", d)])
    row = next(r for r in R["rows"] if r["src"] == "Ours")
    assert row["touches"] == 2 and row["held"] == 1 and row["held_pct"] == 50 and row["past_med_x"] == 0.6 and row["close_beyond"] == 1
    mk = next(r for r in R["rows"] if r["src"] == "Marker")
    assert mk["untouched"] == 1 and mk["touches"] == 0
    h = rs.html({"lines": d["rails"]["lines"] and [dict(x, level=5000.0, name="STR") for x in d["rails"]["lines"]],
                 "mark": {"t": "09:45", "spot": 5000, "straddle": 20, "call": None, "put": None}}, R)
    assert "Rails: reaction or transition" in h and "Running, 1 nights" in h
