"""leg_group_audit.py - written 2026-11-19 (DB review round 1, D13). REPORT ONLY - reads tas_prints.

The tape writer groups same-root prints that land within 2 s (``_LEG_WINDOW_S``) into one
``leg_group`` without checking sizes. On a busy root that can glue unrelated prints together.
This counts, for one session, how many heuristic groups look wrong before anyone changes the rule:

- vendor  : every leg carries the vendor's spread_leg flag (grouped on exact time - trusted)
- matched : heuristic group whose legs all share one size (or simple ratios 1:2, 1:3) - plausible
- mixed   : heuristic group whose sizes don't fit - likely false grouping
- big     : heuristic group with more than 4 legs - likely several trades glued together

Run (laptop, read-only login DATABASE_URL_RO):
    python scripts/leg_group_audit.py 2026-11-18 [--root SPXW]
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from schema_drift import _engine_url, _url

SQL = """
SELECT leg_group, root, size, coalesce(spread_leg, false) AS vend
FROM tas_prints
WHERE trade_date = :d AND leg_group IS NOT NULL {root}
"""


def classify(sizes: list[float], vendor: list[bool]) -> str:
    if all(vendor):
        return "vendor"
    if len(sizes) > 4:
        return "big"
    s = sorted(x for x in sizes if x)
    if not s:
        return "mixed"
    base = s[0]
    ratios = {round(x / base, 6) for x in s}
    return "matched" if ratios <= {1.0, 2.0, 3.0} else "mixed"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("date")
    ap.add_argument("--root")
    a = ap.parse_args()
    from sqlalchemy import create_engine, text

    eng = create_engine(_engine_url(_url()), pool_pre_ping=True)
    q = SQL.format(root="AND root = :r" if a.root else "")
    groups: dict[str, list] = {}
    with eng.connect() as conn:
        for g, root, size, vend in conn.execute(text(q), {"d": a.date, "r": a.root}):
            groups.setdefault(g, [root, [], []])
            groups[g][1].append(float(size or 0))
            groups[g][2].append(bool(vend))
    if not groups:
        print(f"no grouped prints on {a.date}")
        return 0
    kinds = Counter()
    by_root: dict[str, Counter] = {}
    for root, sizes, vend in groups.values():
        k = classify(sizes, vend)
        kinds[k] += 1
        by_root.setdefault(root or "?", Counter())[k] += 1
    heur = kinds["matched"] + kinds["mixed"] + kinds["big"]
    bad = kinds["mixed"] + kinds["big"]
    print(
        f"leg groups {a.date}: {len(groups):,} total | "
        + " | ".join(f"{k} {v:,}" for k, v in kinds.most_common())
    )
    if heur:
        print(f"heuristic groups that look wrong: {bad:,} of {heur:,} ({100 * bad / heur:.1f}%)")
    print("by root (top 10 by groups):")
    for root, c in sorted(by_root.items(), key=lambda kv: -sum(kv[1].values()))[:10]:
        print(f"  {root:<8} " + "  ".join(f"{k}={v}" for k, v in c.most_common()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
