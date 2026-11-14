"""
Nightly check for the TEST cockpit's V9 toggle (created 2026-11-13 overnight; backlog #21 "VIX9D week re-mark").

Production re-marks the 1-7 DTE (Week) book IV by 1/2 x the change in 0DTE ATM IV since the first reading of the day
(cvx_live.py writes v9d = 0, V9D_INTO_MAP = False). The test toggle V9 uses the change in VIX9D (live.json v9dq) instead.
Every 15-minute read-out of the test page stores "wkiv": for each Week-book contract within 2% of spot that has a live IV
(same contract, rescaled to its real time left), the median |morning IV + rule shift - live IV| in vol pts, for both rules.
This script reads those (data/test/readouts_YYYYMMDD.jsonl) and says which rule lands closer to the live chain.

    python v9_score.py                       -> data/test/scores/v9_board.json
    python v9_score.py --data DIR --out DIR  (tests)
score_test.py calls it at the end of its nightly run (DSM 17:20). The maps themselves are also recorded under config "v9"
and scored by score_test.py with the other toggles.
Verdict after MIN_DAYS days: a rule "wins" a day when its median error is lower by >= 0.05 vol pt; V9 is better when it
wins >= 60% of decided days AND its overall median error is lower. Otherwise "no clear winner".
"""
import argparse, glob, json, os, statistics as stats, sys

HERE = os.path.dirname(os.path.abspath(__file__))
MIN_DAYS = 10
TIE = 0.05


def day_rows(path):
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            w = r.get("wkiv") or {}
            if w.get("n") and w.get("half") is not None and w.get("v9") is not None:
                out.append(w)
    return out


def board(days):
    """days = {date: [wkiv, ...]} -> summary dict."""
    per = {}
    for d, rows in sorted(days.items()):
        if not rows:
            continue
        h, v = stats.median(r["half"] for r in rows), stats.median(r["v9"] for r in rows)
        per[d] = {"readouts": len(rows), "half": round(h, 3), "v9": round(v, 3),
                  "winner": "v9" if h - v >= TIE else "half" if v - h >= TIE else "tie"}
    wins = sum(1 for x in per.values() if x["winner"] == "v9")
    loss = sum(1 for x in per.values() if x["winner"] == "half")
    allh = [x["half"] for x in per.values()]
    allv = [x["v9"] for x in per.values()]
    out = {"days": len(per), "min_days": MIN_DAYS, "v9_wins": wins, "half_wins": loss, "ties": len(per) - wins - loss,
           "median_err_half": round(stats.median(allh), 3) if allh else None, "median_err_v9": round(stats.median(allv), 3) if allv else None,
           "per_day": per}
    if len(per) < MIN_DAYS:
        out["verdict"] = "collecting (%d/%d days)" % (len(per), MIN_DAYS)
    else:
        dec = wins + loss
        better = dec and wins / dec >= 0.6 and out["median_err_v9"] < out["median_err_half"]
        out["verdict"] = "VIX9D re-mark closer to the live week chain" if better else "no clear winner - keep production's rule"
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(os.path.dirname(HERE), "data"))
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    outdir = a.out or os.path.join(a.data, "test")
    days = {}
    for f in sorted(glob.glob(os.path.join(outdir, "readouts_*.jsonl"))):
        d = os.path.basename(f)[9:17]
        days["%s-%s-%s" % (d[:4], d[4:6], d[6:])] = day_rows(f)
    b = board(days)
    sdir = os.path.join(outdir, "scores")
    os.makedirs(sdir, exist_ok=True)
    with open(os.path.join(sdir, "v9_board.json"), "w", encoding="utf-8") as fh:
        json.dump(b, fh, indent=1)
    print("v9_score: %s | days %d, median error half %s vs VIX9D %s vol pts" % (b["verdict"], b["days"], b["median_err_half"], b["median_err_v9"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
