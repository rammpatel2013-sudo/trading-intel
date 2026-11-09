"""Unit tests for score_test.py (scoring v1) on constructed price paths. Run: python tests/test_score.py"""
import json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import score_test as S

def px_from(points):  # {minute: price} linear between given (minute, price) knots
    out = {}; ks = sorted(points)
    for a, b in zip(ks, ks[1:]):
        for m in range(a, b + 1):
            out[m] = points[a] + (points[b] - points[a]) * (m - a) / (b - a)
    return out

def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name); return cond

ok = True
t = 600  # 10:00
# S2: approach from below, touch, then held (falls 10 below lo)
px = px_from({600: 7380, 610: 7398, 620: 7385, 960: 7385})
z = S.score_zone(px, t, 7380, 7400, 7402, 960); ok &= check("held from below", z["status"] == "held" and z["side"] == "below")
# broke: goes through and stays 3 minutes >= hi+5
px = px_from({600: 7380, 610: 7399, 615: 7410, 960: 7410})
z = S.score_zone(px, t, 7380, 7400, 7402, 960); ok &= check("broke from below", z["status"] == "broke")
# untouched within 60 min
px = px_from({600: 7380, 700: 7385, 960: 7399})
z = S.score_zone(px, t, 7380, 7400, 7402, 960); ok &= check("untouched within 60 min", z["status"] == "untouched")
# unresolved: touches and drifts inside the band
px = px_from({600: 7380, 610: 7399, 700: 7401, 960: 7401})
z = S.score_zone(px, t, 7380, 7400, 7402, 960); ok &= check("unresolved", z["status"] == "unresolved")
# started inside
z = S.score_zone(px, t, 7401, 7400, 7402, 960); ok &= check("started inside excluded", z["status"] == "started_inside")
# from above, held (rises 10 above hi)
px = px_from({600: 7420, 610: 7403, 620: 7415, 960: 7415})
z = S.score_zone(px, t, 7420, 7400, 7402, 960); ok &= check("held from above", z["status"] == "held" and z["side"] == "above")
# no look-ahead: a touch before the read-out minute is ignored
px = px_from({590: 7400, 600: 7380, 960: 7380})
z = S.score_zone(px, t, 7380, 7400, 7402, 960); ok &= check("ignores prices at/before the read-out", z["status"] == "untouched")
# MFE: from below, touch at 7398 then down to 7385 within 15 min -> MFE 13
px = px_from({600: 7380, 610: 7398, 620: 7385, 960: 7385})
z = S.score_zone(px, t, 7380, 7400, 7402, 960); ok &= check("MFE15 = 13 pts", abs(z["mfe15"] - 13) < 1e-9)
# early close: window stops at the close
px = px_from({720: 7380, 760: 7399, 780: 7399, 800: 7420, 830: 7420})
z = S.score_zone(px, 720, 7380, 7400, 7402, 780); ok &= check("window ends at an early close (prices after 13:00 ignored)", z["status"] == "unresolved")
# lock stats
seq = [("9:45", 9), ("10:00", 3), ("10:15", 7), ("10:30", 4), ("10:45", 2)]
f, l = S.lock_stats(seq); ok &= check("first within 5 = 10:00, lock = 10:30", f == "10:00" and l == "10:30")
f, l = S.lock_stats([("9:45", 2), ("10:00", None)]); ok &= check("a missing prediction breaks the lock", l is None and f == "9:45")
f, l = S.lock_stats([("9:45", 6), ("10:00", 6)]); ok &= check("never within -> no lock", f is None and l is None)
# S3 window: random-walk-ish zigzag, incomplete windows
px = {m: 7400 + (1 if m % 2 else -1) for m in range(570, 961)}
w = S.s3_window(px, 600, 960, 2.0); ok &= check("S3 range/2*im30 = 0.5 on a 1-pt zigzag", abs(w["q"] - 0.5) < 1e-9 and w["ac1"] < -0.9)
ok &= check("S3 incomplete when t+30 > close", S.s3_window(px, 940, 960, 2.0) is None)
ok &= check("S3 missing im30", S.s3_window(px, 600, 960, None) is None)
# end-to-end with a temp folder: one day, two slots, plus a 1DTE forecast pending then scored
with tempfile.TemporaryDirectory() as d:
    out = os.path.join(d, "test"); os.makedirs(out)
    def flow(day, pts):
        with open(os.path.join(d, "flow_%s.ndjson" % day.replace("-", "")), "w") as fh:
            for m, x in sorted(px_from(pts).items()):
                fh.write(json.dumps({"s": "SPX", "t": S.hm(m).zfill(5), "x": x}) + "\n")
    flow("2026-11-05", {570: 7300, 960: 7350})
    flow("2026-11-06", {570: 7380, 600: 7380, 610: 7399, 620: 7385, 960: 7404})
    base = {"v": "score-v1", "date": "2026-11-06", "spot": 7380, "session_close": 960, "im30": 3.0, "term_straddle": {"3": 60}}
    cfg = {"off|P": {"g_spot": -5, "zeros": [{"z": 7401, "lo": 7400, "hi": 7402}], "peaks": [], "troughs": [],
                     "close": {"pred": 7402}, "d1": {"e": "2026-11-09", "exp_t": "2026-11-09T16:00", "pred": 7420}, "fri": None}}
    with open(os.path.join(out, "readouts_20261106.jsonl"), "w") as fh:
        for slot in ("10:00", "10:15"):
            fh.write(json.dumps({**base, "slot": slot, "t": S.tmin(slot), "cfgs": cfg}) + "\n")
        fh.write(json.dumps({**base, "slot": "10:00", "t": 600, "cfgs": {}}) + "\n")   # duplicate slot
    days, s5, board = S.score_all(d, out)
    r = days["2026-11-06"]["configs"]["off|P"]
    ok &= check("e2e: close error 2 -> first and lock at 10:00", r["S4"]["first"] == "10:00" and r["S4"]["lock"] == "10:00")
    ok &= check("e2e: incomplete day flagged (2 of 24 slots)", days["2026-11-06"]["complete"] is False)
    ok &= check("e2e: duplicate slot ignored", len(board["duplicates_ignored"]) == 1)
    ok &= check("e2e: 1DTE forecast pending without the target day's log", list(s5.values())[0]["status"] == "pending")
    ok &= check("e2e: prior session = 11/05", days["2026-11-06"]["prior_session"] == "20261105")
    flow("2026-11-09", {570: 7410, 960: 7418})
    days, s5, board = S.score_all(d, out)
    v = list(s5.values())[0]
    ok &= check("e2e: 1DTE scored once the target close exists (err 2)", v["status"] == "scored" and abs(v["mean_abs_err"] - 2) < 1e-9)
    days2, s52, board2 = S.score_all(d, out)
    b1 = json.dumps(board, sort_keys=True); b2 = json.dumps(board2, sort_keys=True)
    ok &= check("deterministic: same inputs -> same scoreboard", b1.split('"generated"')[0] == b2.split('"generated"')[0] and json.dumps(days, sort_keys=True) == json.dumps(days2, sort_keys=True))
print("ALL OK" if ok else "SOME FAILED"); sys.exit(0 if ok else 1)
