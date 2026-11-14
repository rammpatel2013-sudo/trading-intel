"""Vol Read report (2026-11-13 overnight; Lakha checks + fear release, SPX v1).

HTML to reports/vol_read/ + Telegram document. NAS DSM Mon-Fri 17:45 ET:
  sudo bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_script.sh vol_read_report
Descriptive regime context only (rule 4).
"""
from __future__ import annotations

import argparse
import html
from datetime import date
from pathlib import Path

from trading_intel.config import get_settings
from trading_intel.memory.db import make_session_factory
from trading_intel.synthesis.vol_read import build

_COL = {1: "#16a34a", 0: "#d97706", -1: "#dc2626", None: "#64748b"}
_BAND = {"Constructive": "#16a34a", "Mixed": "#d97706", "Defensive": "#dc2626"}


def render(vr: dict) -> str:
    if not vr.get("found"):
        return f"<p>Vol read: {html.escape(vr.get('reason', 'no data'))}</p>"
    v = vr["verdict"]

    def row(c):
        sc = c["score"]
        mark = {1: "+1", 0: "0", -1: "-1", None: "info"}[sc]
        return (f'<tr><td style="color:{_COL[sc]};font-weight:800;font-size:18px">{mark}</td>'
                f'<td><b>{html.escape(c["name"])}</b><br>{html.escape(str(c["value"]))} - '
                f'<b style="color:{_COL[sc]}">{html.escape(c["verdict"])}</b>'
                f'<div style="color:#475569;font-size:14px">{html.escape(c["line"])}</div></td></tr>')
    rows = "".join(row(c) for c in vr["checks"] + vr["info"])
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Vol read {vr['as_of']}</title><style>body{{font-family:system-ui;margin:16px;font-size:16px;color:#1a2027}}
td{{padding:8px;border-bottom:1px solid #e2e8f0;vertical-align:top}}</style></head><body>
<h1>SPX vol read - {vr['as_of']}</h1>
<div style="border:3px solid {_BAND[v['band']]};border-radius:12px;padding:14px;font-size:22px">
<b style="color:{_BAND[v['band']]}">{v['band']} {v['score']:+d}/{v['of']}</b><br>{html.escape(v['text'])}</div>
<p style="font-size:18px">{html.escape(vr['fear_release']['line'])}</p>
<table style="width:100%;border-collapse:collapse">{rows}</table>
<p style="color:#64748b">History: {vr['n_sessions']} sessions (percentiles warm up to 252). Descriptive only - not a signal.</p>
</body></html>"""


def main() -> None:
    p = argparse.ArgumentParser(description="SPX vol read (Lakha checks + fear release)")
    p.add_argument("--no-telegram", action="store_true")
    a = p.parse_args()
    s = get_settings()
    with make_session_factory(s)() as session:
        vr = build(session)
    out = Path("reports/vol_read")
    out.mkdir(parents=True, exist_ok=True)
    f = out / f"vol_read_{date.today():%Y%m%d}.html"
    f.write_text(render(vr), encoding="utf-8")
    print("wrote", f, vr.get("verdict"))
    if not a.no_telegram and vr.get("found"):
        from trading_intel.clients.telegram import TelegramClient
        from trading_intel.synthesis.vol_read import brief_line
        t = TelegramClient(s)
        t.send_message(brief_line(vr))
        t.send_document(f, caption="SPX vol read")


if __name__ == "__main__":
    main()
