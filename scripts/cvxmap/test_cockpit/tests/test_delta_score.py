"""Unit tests for delta_score.py (Delta map levels, rules dl-v1: plain + shadow modes; created 2026-11-13). Run: python tests/test_delta_score.py"""
import json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import delta_score as D


def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name); return cond


def px_from(points):
    out = {}; ks = sorted(points)
    for a, b in zip(ks, ks[1:]):
        for m in range(a, b + 1):
            out[m] = points[a] + (points[b] - points[a]) * (m - a) / (b - a)
    return out


ok = True
lv = [{"rank": 1, "p": 7400.0, "cls": "hold", "dir": "sell", "es5": 900}, {"rank": 2, "p": 7350.0, "cls": "speed", "dir": "sell", "es5": 500}]
# placebo: same side, 10 pts further first; never within 5 pts of a numbered level, never within 3 of spot
ok &= check("placebo +10 above", D.placebo(7400.0, 7380.0, lv) == 7410.0)
ok &= check("placebo skips a numbered level", D.placebo(7360.0, 7380.0, lv + [{"p": 7370.0}]) == 7375.0 or D.placebo(7360.0, 7380.0, lv + [{"p": 7370.0}]) is None)
ok &= check("placebo stays on the same side", D.placebo(7385.0, 7380.0, []) == 7395.0)
# a day: price rises to 7400 and turns back (hold level held), later falls through 7350 (speed level broke)
px = px_from({571: 7380, 600: 7380, 610: 7399, 625: 7385, 700: 7360, 712: 7349, 720: 7340, 960: 7340})
recs = [(600, 7380.0, lv, "10:00", 960), (615, 7395.0, lv, "10:15", 960)]
r = D.score_day(px, recs)
ok &= check("one score per level per day", len(r["levels"]) == 2)
st = {x["p"]: x["status"] for x in r["levels"]}
ok &= check("hold level held", st[7400.0] == "held")
ok &= check("speed level untouched in 60 min from 10:00", st[7350.0] == "untouched")
ok &= check("class tallies", r["by_class"]["hold"]["level"]["held"] == 1 and r["by_class"]["speed"]["level"]["untouched"] == 1)
# files end to end + determinism + rules filter
with tempfile.TemporaryDirectory() as td:
    data = td; out = os.path.join(td, "test"); os.makedirs(out)
    with open(os.path.join(data, "flow_20261116.ndjson"), "w") as fh:
        for m, x in sorted(px.items()):
            fh.write(json.dumps({"t": "%02d:%02d" % divmod(m, 60), "s": "SPX", "x": x}) + "\n")
    with open(os.path.join(out, "readouts_20261116.jsonl"), "w") as fh:
        for t, sp, L, slot, cl in recs:
            fh.write(json.dumps({"v": "score-v1", "date": "2026-11-16", "slot": slot, "t": t, "spot": sp, "session_close": cl,
                                 "dl": {"v": "dl-v1", "spot": sp, "levels": L, "levels_sg": L[:1]}}) + "\n")
        fh.write(json.dumps({"v": "score-v1", "date": "2026-11-16", "slot": "10:30", "t": 630, "spot": 7380,
                             "dl": {"v": "dl-v9", "spot": 7380, "levels": lv}}) + "\n")
    a = D.score_all(data, out); b = D.score_all(data, out)
    ok &= check("deterministic", json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True))
    ok &= check("other rule versions ignored", len(a[0]["2026-11-16"]["plain"]["levels"]) == 2)
    ok &= check("shadow mode scored on its own levels", len(a[0]["2026-11-16"]["shadow"]["levels"]) == 1)
    ok &= check("board collecting below 20 days", a[1]["modes"]["plain"]["by_class"]["hold"]["verdict"] == "collecting")
    D.main(["--data", data, "--out", out])
    ok &= check("files written", os.path.exists(os.path.join(out, "scores", "delta_20261116.json")) and os.path.exists(os.path.join(out, "scores", "delta_board.json")))
print("ALL OK" if ok else "SOME FAILED"); sys.exit(0 if ok else 1)
