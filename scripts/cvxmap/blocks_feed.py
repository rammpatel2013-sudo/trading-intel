#!/usr/bin/env python
"""blocks_feed.py - live index big-trade STRUCTURES for the TOS dashboard.

Polls the ConvexValue tape (via your trading-intel client), clusters prints into
structures with the SAME rules as scripts/index_bigtrades_report.py (legs within
(root, +/-2s), matched size = one structure - NOT summed as directional flow),
and writes blocks.json next to the dashboard so the "Block & sweep track" panel
shows real grouped prints.

Run it alongside the dashboard (in a second terminal):
    python C:\\TOS-Dashboard\\blocks_feed.py
Needs your trading-intel checkout + its ConvexValue credentials in that .env.
"""
import os as _os
from pathlib import Path as _P
_TI_DEFAULT = r"C:\\Users\\drmit\\PycharmProjects\\trading-intel"
_DASH_DEFAULT = r"C:\\tos-dashboard"
_TI  = _os.environ.get("TI_DIR")  or _TI_DEFAULT
_DASH= _os.environ.get("DASH_DIR") or _DASH_DEFAULT
import json
import os
import sys
import time
from pathlib import Path

# --- point Python at your trading-intel checkout so we can reuse its code ------
TI = _TI
OUT = _P(_DASH) / "blocks.json"
POLL_S = 60                       # tape is rate-limited; 1 pull/min is plenty
INDEX_ROOTS = {"SPX", "SPXW", "SPY", "QQQ"}
PREMIUM_FLOOR = {"SPX": 250_000, "SPXW": 250_000, "SPY": 100_000, "QQQ": 100_000}

sys.path.insert(0, TI)
sys.path.insert(0, os.path.join(TI, "scripts"))
os.chdir(TI)                      # so get_settings() finds the .env

from trading_intel.config import get_settings          # noqa: E402
from trading_intel.timeutils import eastern_now         # noqa: E402
import index_bigtrades_report as ibt                     # noqa: E402


def _leg(l):
    sz = l.get("size") or 0
    prem = float(l.get("premium") or 0)
    return {
        "cp": (str(l.get("opt_kind") or "")[:1].upper()),
        "k": l.get("strike"),
        "exp": str(l.get("expiration"))[:10],
        "sz": sz,
        "premK": round(prem / 1e3),
    }


def build_payload(settings):
    now = eastern_now()
    prints = ibt._fetch_prints(settings, limit=300)
    idx = [
        p for p in prints
        if str(p.get("root", "")).upper() in INDEX_ROOTS
        and float(p.get("premium") or 0) >= PREMIUM_FLOOR.get(str(p.get("root")).upper(), 1e9)
    ]
    structs = ibt.group_structures(idx, now.date())
    structs.sort(key=lambda s: s.get("premium") or 0, reverse=True)
    out = []
    for s in structs[:30]:
        root = str(s.get("root") or "")
        sym = "SPX" if root in ("SPX", "SPXW") else root
        kind = s.get("type") or "?"
        if s.get("has_sweep"):
            tag = "SWEEP"
        elif s.get("is_financing"):
            tag = "FIN"
        elif s.get("is_multi"):
            tag = kind.upper()[:6]
        elif s.get("is_directional"):
            tag = "DIR"
        else:
            tag = "BLK"
        out.append({
            "sym": sym,
            "kind": kind,
            "tag": tag,
            "label": s.get("label") or f"{root} {kind}",
            "prem": round(float(s.get("premium") or 0) / 1e6, 2),   # $M
            "tenor": s.get("tenor"),
            "dte": s.get("dte"),
            "n": s.get("n"),
            "multi": bool(s.get("is_multi")),
            "financing": bool(s.get("is_financing")),
            "directional": bool(s.get("is_directional")),
            "sweep": bool(s.get("has_sweep")),
            "legs": [_leg(l) for l in s.get("legs", [])[:6]],
        })
    return {"asof": now.strftime("%Y-%m-%d %H:%M ET"), "structures": out}


def main():
    settings = get_settings()
    print(f"blocks_feed: writing {OUT} every {POLL_S}s (Ctrl+C to stop)")
    while True:
        try:
            data = build_payload(settings)
            OUT.write_text(json.dumps(data), encoding="utf-8")
            print(f"  {data['asof']}: {len(data['structures'])} structures")
        except Exception as exc:                      # noqa: BLE001
            print("  error:", exc)
        time.sleep(POLL_S)


if __name__ == "__main__":
    main()
