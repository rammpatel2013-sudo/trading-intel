"""
Nightly scorer for the TEST cockpit's Delta map levels (created 2026-11-13; rules dl-v1: levels with Shadow off AND on,
a change makes dl-v2 forward only).

Reads the "dl" field of the frozen 15-minute read-outs (data/test/readouts_YYYYMMDD.jsonl, written by the test page while it
is open: fixed config = SPX, OI + vol, All book, production maths, Smile off; "levels" = IV held, "levels_sg" = Alma's
shadow greeks, IV moving with price by the measured beta) and the SPX minute path in the production
minute log (data/flow_YYYYMMDD.ndjson). Deterministic: recomputed from scratch on each run, never edits a read-out.

    python delta_score.py                       -> data/test/scores/delta_YYYYMMDD.json + delta_board.json
    python delta_score.py --data DIR --out DIR  (tests)
score_test.py calls it at the end of its nightly run (DSM 17:20), so no extra task is needed.

Rules dl-v1 (scored separately for each mode: plain = IV held, shadow = IV moves with price)
  Levels   #1-#6 of each read-out = the peaks of |dealer gamma| (most hedging per 5 pts), each tagged
           hold  = long gamma at the level: dealers trade against the move into it (should turn price back),
           speed = short gamma: dealers trade with the move (should let price through).
           One score per level per day: the first read-out that lists it (level rounded to 2.5 pts).
  Outcome  score_test.score_zone with lo = hi = level (same S2 rules as scoring v1): touch within 2 pts inside 60 min or the
           close; after the touch: held = 10 pts back out the approach side, broke = 3 minutes in a row 5+ pts through;
           first event wins, else unresolved (reported, not in the rate). Started inside / untouched are counted apart.
  Placebo  for each level, a level at the same side of spot moved by +10, -10, +15 or -15 pts (first that stays at least 3
           pts from spot and 5 pts from every numbered level of that read-out), scored the same way and filed under the
           same class. Plain round-25 levels are already scored by score_test.py (plain_levels).
  Verdict  (shown once each class has 40+ resolved touches over 20+ days) hold class: held rate >= placebo + 10 pts;
           speed class: broke rate >= placebo + 10 pts. Otherwise "no edge yet".
"""
import argparse, glob, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import score_test as S  # noqa: E402

RULES = "dl-v1"
MODES = (("plain", "levels"), ("shadow", "levels_sg"))
OFFS = (10.0, -10.0, 15.0, -15.0)
MIN_SPOT, MIN_LVL, ROUND = 3.0, 5.0, 2.5
MIN_N, MIN_DAYS, EDGE = 40, 20, 0.10


def load_dl(outdir, field="levels"):
    """{date: [(t, spot, levels, slot, close)]} for one mode; first record per (date, slot) counts (as score_test)."""
    out, seen = {}, set()
    for f in sorted(glob.glob(os.path.join(outdir, "readouts_*.jsonl"))):
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if not r.get("date") or not r.get("slot") or (r["date"], r["slot"]) in seen:
                    continue
                seen.add((r["date"], r["slot"]))
                dl = r.get("dl") or {}
                if dl.get("v") != RULES or dl.get("err") or not dl.get(field):
                    continue
                out.setdefault(r["date"], []).append((r["t"], dl.get("spot") or r.get("spot"), dl[field], r["slot"],
                                                      r.get("session_close") or 16 * 60))
    for d in out:
        out[d].sort()
    return out


def placebo(level, spot, levels):
    side = 1 if level > spot else -1
    for o in OFFS:
        p = level + o
        if (p - spot) * side < MIN_SPOT:
            continue
        if any(abs(p - L["p"]) < MIN_LVL for L in levels):
            continue
        return p
    return None


def _tally():
    return {"held": 0, "broke": 0, "unresolved": 0, "untouched": 0, "inside": 0}


def _add(t, z):
    t[{"started_inside": "inside"}.get(z["status"], z["status"])] += 1


def _rates(t):
    n = t["held"] + t["broke"]
    return dict(t, resolved=n, held_rate=round(t["held"] / n, 3) if n else None, broke_rate=round(t["broke"] / n, 3) if n else None)


def score_day(px, recs):
    seen, rows = set(), []
    cls = {c: {"level": _tally(), "placebo": _tally()} for c in ("hold", "speed")}
    for t, spot, levels, slot, close in recs:
        for L in levels:
            key = round(L["p"] / ROUND) * ROUND
            if key in seen:
                continue
            seen.add(key)
            c = L.get("cls") if L.get("cls") in cls else ("hold" if L.get("dir") in ("absorbs",) else "speed")
            z = S.score_zone(px, t, spot, L["p"], L["p"], close)
            _add(cls[c]["level"], z)
            pp = placebo(L["p"], spot, levels)
            zp = S.score_zone(px, t, spot, pp, pp, close) if pp is not None else None
            if zp:
                _add(cls[c]["placebo"], zp)
            rows.append({"slot": slot, "rank": L.get("rank"), "p": L["p"], "cls": c, "dir": L.get("dir"), "es5": L.get("es5"),
                         "status": z["status"], "placebo": pp, "placebo_status": zp["status"] if zp else None})
    return {"levels": rows, "by_class": {c: {k: _rates(v) for k, v in d.items()} for c, d in cls.items()}}


def board(days):
    tot = {c: {"level": _tally(), "placebo": _tally()} for c in ("hold", "speed")}
    for rec in days.values():
        for c, d in rec.get("by_class", {}).items():
            for k, t in d.items():
                for f in _tally():
                    tot[c][k][f] += t[f]
    out = {"rules": RULES, "days": len(days), "by_class": {}}
    for c, d in tot.items():
        lv, pl = _rates(d["level"]), _rates(d["placebo"])
        rate = "held_rate" if c == "hold" else "broke_rate"
        verdict = "collecting"
        if len(days) >= MIN_DAYS and lv["resolved"] >= MIN_N and pl["resolved"] >= MIN_N:
            verdict = "edge" if (lv[rate] or 0) >= (pl[rate] or 0) + EDGE else "no edge yet"
        out["by_class"][c] = {"level": lv, "placebo": pl, "rate_used": rate, "verdict": verdict}
    return out


def score_all(data, outdir):
    days, boards = {}, {}
    for mode, field in MODES:
        for day, recs in sorted(load_dl(outdir, field).items()):
            rec = days.setdefault(day, {"date": day, "rules": RULES})
            px = S.load_prices(data, day)
            if not px:
                rec["status"] = "no minute log yet"
                continue
            rec[mode] = score_day(px, recs)
        boards[mode] = board({d: r[mode] for d, r in days.items() if mode in r})
    return days, {"rules": RULES, "modes": boards}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=S.DATA_DEF)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    outdir = a.out or os.path.join(a.data, "test")
    days, b = score_all(a.data, outdir)
    sdir = os.path.join(outdir, "scores")
    os.makedirs(sdir, exist_ok=True)
    for day, rec in days.items():
        with open(os.path.join(sdir, "delta_%s.json" % day.replace("-", "")), "w") as fh:
            json.dump(rec, fh, indent=1)
    with open(os.path.join(sdir, "delta_board.json"), "w") as fh:
        json.dump(b, fh, indent=1)
    for mode, mb in b["modes"].items():
        h, s = mb["by_class"]["hold"], mb["by_class"]["speed"]
        print("delta levels %s: %d day(s) | hold held %s vs placebo %s (%s) | speed broke %s vs placebo %s (%s)" % (
            mode, mb["days"], h["level"]["held_rate"], h["placebo"]["held_rate"], h["verdict"],
            s["level"]["broke_rate"], s["placebo"]["broke_rate"], s["verdict"]))
    print("delta scores -> %s" % sdir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
