#!/usr/bin/env python
"""One-shot (2026-11-06): add the next expiry's per-strike volume ("n1") to a day's flow log.

The live writer (cvx_live.py) writes "n1" on every minute from 2026-11-06 on. This fills it
in for minutes written before that, from the banked raw file, WITHOUT touching any other key
of any line (so the frozen map's inputs are unchanged). Lines that already carry "n1" are
left alone. Safe to re-run.

Do NOT use `cvx_live.py --rebuild` for this: it stamps every rebuilt line with the time the
rebuild runs, not the time of the pull.

Run after the writer has stopped for the day (17:00 ET), or for past days any time:
    python scripts/cvxmap/n1_backfill.py              # today
    python scripts/cvxmap/n1_backfill.py 20261105 20261104
"""
from __future__ import annotations

import datetime
import gzip
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from cvx_live import DATA, STEP, decode, exp_date, next_expiry_vol, now_et  # noqa: E402


def pull_minutes(raw_path: str, day: datetime.date) -> dict[str, list[tuple[str, dict]]]:
    """{sym: [(HH:MM of the pull, n1), ...]} in pull order."""
    out: dict[str, list[tuple[str, dict]]] = {}
    with gzip.open(raw_path, "rt", encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            ts = str(rec.get("ts") or "")
            if len(ts) < 16 or "symbols" not in rec:
                continue
            hhmm = ts[11:16]
            try:
                dec = decode(rec)
            except Exception:
                continue
            for sym, d in dec.items():
                if sym not in STEP or not d.get("exps"):
                    continue
                exp0 = exp_date(d["exps"][0][0])
                nx = next_expiry_vol(d, exp0, d["spot"], STEP[sym], day)
                if nx:
                    out.setdefault(sym, []).append((hhmm, nx))
    return out


def backfill(ymd: str) -> None:
    raw = os.path.join(DATA, "raw_%s.ndjson.gz" % ymd)
    flow = os.path.join(DATA, "flow_%s.ndjson" % ymd)
    if not (os.path.exists(raw) and os.path.exists(flow)):
        print("%s: raw or flow file missing - skipped" % ymd)
        return
    day = datetime.date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:]))
    pulls = pull_minutes(raw, day)
    added = kept = none = 0
    lines = []
    for line in open(flow, encoding="utf-8"):
        s = line.rstrip("\n")
        if not s.strip():
            continue
        try:
            j = json.loads(s)
        except Exception:
            lines.append(s)
            continue
        if "n1" in j or j.get("_bf"):
            kept += 1
            lines.append(s)
            continue
        best = None   # the latest pull at or before the line's minute (volume is cumulative)
        for hhmm, nx in pulls.get(j.get("s"), []):
            if hhmm <= j.get("t", ""):
                best = nx
            else:
                break
        if best is None:
            none += 1
            lines.append(s)
            continue
        j["n1"] = best
        lines.append(json.dumps(j, separators=(",", ":")))
        added += 1
    tmp = flow + ".n1tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    os.replace(tmp, flow)
    print("%s: n1 added %d, already had / backfilled-CVForge %d, no pull found %d" % (ymd, added, kept, none))


if __name__ == "__main__":
    days = [a for a in sys.argv[1:] if a.isdigit() and len(a) == 8] or [now_et().strftime("%Y%m%d")]
    for ymd in days:
        backfill(ymd)
