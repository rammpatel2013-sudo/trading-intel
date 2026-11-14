"""Nightly gamma-change split (Tao T3) - 2026-11-13 overnight. Telegram line + JSON in reports/gamma_split/.
NAS DSM Mon-Fri 17:50 ET: sudo bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_script.sh gamma_split_report
Grades yesterday's projected post-expiry book against today's book at the same price (error kept in errors.json)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from trading_intel.config import get_settings
from trading_intel.market.gamma_split import plain, split
from trading_intel.memory.db import make_session_factory

OUT = Path("reports/gamma_split")


def main() -> None:
    a = argparse.ArgumentParser()
    a.add_argument("--no-telegram", action="store_true")
    a.add_argument("--symbol", default="SPX")
    args = a.parse_args()
    s = get_settings()
    with make_session_factory(s)() as session:
        r = split(session, args.symbol)
    OUT.mkdir(parents=True, exist_ok=True)
    line = plain(r)
    errs_p = OUT / "errors.json"
    errs = json.loads(errs_p.read_text()) if errs_p.exists() else []
    last_p = OUT / "last.json"
    if r.get("found") and last_p.exists():
        last = json.loads(last_p.read_text())
        if last.get("book") != r["book"] and last.get("projected_after_next") is not None:
            err = r["gamma_now"] - last["projected_after_next"]
            errs.append({"book": r["book"], "projected": last["projected_after_next"], "actual": r["gamma_now"],
                         "error": err})
            errs_p.write_text(json.dumps(errs[-250:], indent=1))
            line += f" Yesterday's post-expiry projection was {last['projected_after_next']:+.2f}; actual {r['gamma_now']:+.2f}."
    if r.get("found"):
        last_p.write_text(json.dumps(r))
        (OUT / f"split_{r['book']}.json").write_text(json.dumps(r, indent=1))
    print(line)
    if not args.no_telegram and r.get("found"):
        from trading_intel.clients.telegram import TelegramClient
        TelegramClient(s).send_message("Gamma change (why): " + line)


if __name__ == "__main__":
    main()
