"""Compare a CVForge shadow file against the Convex greeks_snapshots of the same evening.

Created 2026-10-28. Read-only: reads the shadow JSONL and the DB, writes a CSV.

Per symbol it lines up the CVForge numbers with the LATEST Convex row on
``--convex-date`` at/after ``--after`` (default 16:00, i.e. the 18:10 EOD run), then
reports, in plain numbers:
  * spot gap (%), so a timing mismatch is visible before anything else is judged
  * gamma (GEX) and delta (DEX) totals: sign agreement and size ratio
  * gamma flip: distance between the two flips as % of spot
  * ATM IV gap in vol points
  * vanna/charm (VEX/CHEX): sign agreement only - absolute scale is known to differ
    (CVForge synthesizes them with Black-Scholes, ADR-004)

Run:  python scripts/cvforge_compare.py [--shadow FILE] [--convex-date YYYY-MM-DD] [--after HH:MM]
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics as st
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _sign_agree(a, b):
    if a is None or b is None or a == 0 or b == 0:
        return None
    return (a > 0) == (b > 0)


def _ratio(a, b):
    if a is None or b is None or b == 0:
        return None
    return a / b


def _pct(xs, q):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--shadow", default="", help="shadow JSONL (default: newest in reports/cvf_shadow)")
    ap.add_argument("--convex-date", default="", help="date of the Convex rows (default: shadow date)")
    ap.add_argument("--after", default="16:00", help="only Convex rows at/after this ET time")
    ap.add_argument("--out-dir", default="reports/cvf_shadow")
    a = ap.parse_args(argv)

    out_dir = Path(a.out_dir)
    shadow = Path(a.shadow) if a.shadow else max(out_dir.glob("greeks_*.jsonl"), default=None)
    if shadow is None or not shadow.exists():
        print("no shadow file found", file=sys.stderr)
        return 1
    rows = [json.loads(line) for line in shadow.read_text(encoding="utf-8").splitlines() if line.strip()]
    cvf = {r["symbol"]: r for r in rows if not r.get("error")}
    sdate = rows[0]["ts"][:10] if rows else date.today().isoformat()
    cdate = a.convex_date or sdate

    from sqlalchemy import text

    from trading_intel.config import get_settings
    from trading_intel.memory.db import make_session_factory

    q = text("""
        select distinct on (symbol) symbol, ts, spot, gex_total, dex_total, vex_total,
               chex_total, gex_flip, atm_iv
        from greeks_snapshots
        where source = 'convex' and ts::date = :d and ts::time >= :after
        order by symbol, ts desc
    """)
    with make_session_factory(get_settings())() as s:
        cvx = {r.symbol: r for r in s.execute(q, {"d": cdate, "after": a.after + ":00"})}

    out_rows = []
    for sym in sorted(set(cvf) & set(cvx)):
        c, x = cvf[sym], cvx[sym]
        sp_c, sp_x = _f(c.get("spot")), _f(x.spot)
        flip_c, flip_x = _f(c.get("gex_flip")), _f(x.gex_flip)
        ref = sp_x or sp_c
        out_rows.append({
            "symbol": sym,
            "convex_ts": str(x.ts)[:16],
            "spot_gap_pct": (100 * (sp_c - sp_x) / sp_x) if sp_c and sp_x else None,
            "gex_cvf": _f(c.get("gex_total")), "gex_cvx": _f(x.gex_total),
            "gex_sign_agree": _sign_agree(_f(c.get("gex_total")), _f(x.gex_total)),
            "gex_ratio": _ratio(_f(c.get("gex_total")), _f(x.gex_total)),
            "dex_sign_agree": _sign_agree(_f(c.get("dex_total")), _f(x.dex_total)),
            "dex_ratio": _ratio(_f(c.get("dex_total")), _f(x.dex_total)),
            "flip_cvf": flip_c, "flip_cvx": flip_x,
            "flip_gap_pct_spot": (100 * abs(flip_c - flip_x) / ref) if flip_c and flip_x and ref else None,
            "iv_gap_volpts": (100 * (_f(c.get("atm_iv")) - _f(x.atm_iv)))
            if _f(c.get("atm_iv")) is not None and _f(x.atm_iv) is not None else None,
            "vex_sign_agree": _sign_agree(_f(c.get("vex_total")), _f(x.vex_total)),
            "chex_sign_agree": _sign_agree(_f(c.get("chex_total")), _f(x.chex_total)),
        })

    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"compare_{sdate.replace('-', '')}.csv"
    if out_rows:
        with csv_path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(out_rows[0]))
            w.writeheader()
            w.writerows(out_rows)

    def share(key):
        v = [r[key] for r in out_rows if r[key] is not None]
        return f"{100 * sum(v) / len(v):.0f}% of {len(v)}" if v else "n/a"

    def med_abs(key):
        v = [abs(r[key]) for r in out_rows if r[key] is not None]
        return f"{st.median(v):.2f}" if v else "n/a"

    ratios = [abs(r["gex_ratio"]) for r in out_rows if r["gex_ratio"] is not None]
    print(f"shadow {shadow.name} ({len(cvf)} names ok) vs Convex {cdate} >= {a.after} ({len(cvx)} names)")
    print(f"paired names: {len(out_rows)}")
    print(f"spot gap %       median |x| {med_abs('spot_gap_pct')}   (large = different times, judge nothing else)")
    print(f"GEX sign agrees  {share('gex_sign_agree')}   |ratio| p25/p50/p75 "
          f"{_pct(ratios, .25) and round(_pct(ratios, .25), 2)} / {_pct(ratios, .5) and round(_pct(ratios, .5), 2)}"
          f" / {_pct(ratios, .75) and round(_pct(ratios, .75), 2)}")
    print(f"DEX sign agrees  {share('dex_sign_agree')}")
    print(f"flip gap         median {med_abs('flip_gap_pct_spot')} % of spot")
    print(f"ATM IV gap       median |x| {med_abs('iv_gap_volpts')} vol pts")
    print(f"VEX sign agrees  {share('vex_sign_agree')}   CHEX sign agrees {share('chex_sign_agree')}")
    worst = sorted((r for r in out_rows if r["flip_gap_pct_spot"] is not None),
                   key=lambda r: -r["flip_gap_pct_spot"])[:8]
    if worst:
        print("largest flip gaps: " + ", ".join(f"{r['symbol']} {r['flip_gap_pct_spot']:.1f}%" for r in worst))
    if out_rows:
        print(f"csv -> {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
