#!/usr/bin/env python
# ruff: noqa: E501, RUF100
"""vomma_zomma_report.py - 2026-11-13 (overnight run), Claude for Mithil. #60 group: vomma / zomma per expiry vs baseline.

For every stored SPX close book (oi_chain_eod convex_eod, one per day) not yet in reports/vomma_zomma/history.json,
sums dealer gamma / vomma / zomma per DTE bucket and per expiry on the naive book (trading_intel/market/vomma_zomma.py,
tested), then flags: hedge-tenor (31-90 DTE) vomma >= 1.45x its 20-book median while |gamma| shrinks; the nearest
monthly's zomma halving over 5 books into OpEx. First run back-fills the stored books (~90 days; a minute or two).
Writes reports/vomma_zomma/latest.json + history.json; Telegram line (--no-telegram to skip).

RUN (NAS, DSM task "TI vomma zomma", Mon-Fri 18:25, user root):
    bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_script.sh vomma_zomma_report
Read-only on the DB, no vendor calls. Descriptor only (rule 4).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
for p in (str(REPO), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from trading_intel.market import vomma_zomma as vz  # noqa: E402

OUT = REPO / "reports" / "vomma_zomma"
KEEP = 260


def load_hist() -> list[dict]:
    try:
        return json.loads((OUT / "history.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def update(
    db, hist: list[dict]
) -> list[dict]:  # noqa: ANN001 - vs_flip_score.Db (read-only DB helper)
    have = {h["date"][:10] for h in hist}
    eod = {}
    for ts, kind in db.books():
        if kind == "eod":
            eod.setdefault(ts.date(), ts)
    for d in sorted(eod):
        if d.isoformat() in have:
            continue
        ts = eod[d]
        spot = db.spot(ts)
        rows = [(k[0], k[1], k[2], v["oi"], v["iv"]) for k, v in db.rows(ts, "eod").items()]
        if not spot or not rows:
            continue
        ex = vz.exposures(rows, spot, ts)
        hist.append({"date": d.isoformat(), "spot": round(spot, 2), **ex})
    hist.sort(key=lambda h: h["date"])
    return hist[-KEEP:]


def text(f: dict, cur: dict) -> str:
    b = cur["buckets"]
    L = [
        f"<b>Vomma / zomma</b> SPX close book {cur['date']} (naive book; $bn gamma per 1%, $M vega change per vol pt, $bn gamma change per vol pt)"
    ]
    for name in b:
        x = b[name]
        L.append(
            f"  {name:>5} DTE: gamma {x['gex_bn']:+.2f} · vomma {x['vomma_mn']:+.2f} · zomma {x['zomma_bn']:+.3f}"
        )
    if f.get("vomma_vs_base") is not None:
        L.append(
            f"  31-90 DTE vomma {f['vomma_vs_base']}x its 20-book median; total |gamma| {f['gex_vs_base']}x"
        )
    if f.get("monthly_zomma_ratio_5") is not None:
        L.append(f"  {f['monthly']} monthly zomma {f['monthly_zomma_ratio_5']}x of 5 books ago")
    L += ["  ⚑ " + x for x in f.get("flags") or []] or ["  no flags"]
    L.append("<i>Descriptor, not a signal.</i>")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-telegram", action="store_true")
    a = ap.parse_args(argv)
    from vs_flip_score import Db

    hist = update(Db(), load_hist())
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "history.json").write_text(json.dumps(hist, separators=(",", ":")), encoding="utf-8")
    if not hist:
        print("vomma_zomma.skip no stored close books")
        return 0
    f = vz.flags(hist)
    (OUT / "latest.json").write_text(
        json.dumps({"flags": f, "book": hist[-1]}, indent=1), encoding="utf-8"
    )
    txt = text(f, hist[-1])
    print(txt)
    if not a.no_telegram:
        from trading_intel.clients.telegram import TelegramClient
        from trading_intel.config import get_settings

        TelegramClient(get_settings()).send_message(txt)
    print(f"vomma_zomma.done books={len(hist)} flags={len(f.get('flags') or [])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
