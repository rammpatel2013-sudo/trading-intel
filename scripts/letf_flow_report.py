"""LETF flow panel (2026-11-13 overnight; backlog #50, Mithil "keep").

From the banked letf_shares_snapshots: per day, net creations ($) in BULL leveraged funds vs BEAR
(inverse) funds = which way the leveraged crowd is leaning, and today's forced end-of-day rebalance $
per underlying (funds must buy into strength / sell into weakness at the close). One HTML page with
big bars + one Telegram line. NAS DSM Mon-Fri 17:35 ET:
  sudo bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_script.sh letf_flow_report
Descriptor only (rule 4).
"""
from __future__ import annotations

import argparse
import html
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from trading_intel.etf_flows.descriptors import SharesRow, compute_symbol_flows
from trading_intel.etf_flows.registry import meta_for

E = html.escape


def summarize(rows_by_sym: dict[str, list[SharesRow]], days: int = 20) -> dict:
    by_day: dict[date, dict[str, float]] = defaultdict(lambda: {"bull": 0.0, "bear": 0.0})
    reb: dict[str, float] = defaultdict(float)
    last_day = None
    pts_all = []
    for sym, rows in rows_by_sym.items():
        m = meta_for(sym)
        if m is None:
            continue
        pts = compute_symbol_flows(sym, rows, leverage=m.leverage)
        pts_all.append((m, pts))
        for p in pts:
            if p.net_issuance_usd is not None:
                by_day[p.ts]["bull" if m.leverage > 0 else "bear"] += p.net_issuance_usd
        if pts:
            last_day = max(last_day or pts[-1].ts, pts[-1].ts)
    for m, pts in pts_all:
        for p in pts:
            if p.ts == last_day and p.rebalance_notional is not None:
                reb[m.underlying] += p.rebalance_notional
    ds = sorted(by_day)[-days:]
    return {"days": [(d.isoformat(), by_day[d]["bull"], by_day[d]["bear"]) for d in ds],
            "rebalance": dict(sorted(reb.items(), key=lambda kv: -abs(kv[1]))), "as_of": last_day}


def render(sm: dict) -> str:
    rows = sm["days"]
    mx = max((max(abs(b), abs(r)) for _, b, r in rows), default=1) or 1
    w, h = 760, 260
    bw = w / max(len(rows), 1)
    bars = []
    for i, (d, b, r) in enumerate(rows):
        x = i * bw
        for j, (v, col, lab) in enumerate(((b, "#16a34a", "bull funds"), (r, "#dc2626", "bear funds"))):
            hh = abs(v) / mx * (h / 2 - 20)
            y = h / 2 - hh if v >= 0 else h / 2
            bars.append(f'<rect x="{x + 2 + j * bw * 0.45:.1f}" y="{y:.1f}" width="{bw * 0.42:.1f}" height="{hh:.1f}" fill="{col}">'
                        f'<title>{E(d)} {lab}: {v / 1e6:+,.0f}M net creations</title></rect>')
    reb = "".join(f'<tr><td><b>{E(k)}</b></td><td style="color:{"#16a34a" if v > 0 else "#dc2626"};font-size:20px">'
                  f'{"buy" if v > 0 else "sell"} ${abs(v) / 1e6:,.0f}M</td></tr>' for k, v in list(sm["rebalance"].items())[:8])
    last = rows[-1] if rows else None
    lean = "" if not last else ("leaning BULL" if last[1] - last[2] > 0 else "leaning BEAR")
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>LETF flows {E(str(sm.get('as_of')))}</title><style>body{{font-family:system-ui;margin:14px;font-size:17px}}td{{padding:6px 12px}}</style></head><body>
<h1>Leveraged ETF flows - {E(str(sm.get('as_of')))}</h1>
<p style="font-size:20px">Latest day: the leveraged crowd is <b>{lean}</b>. Bars: money put INTO (above the line) or taken OUT of
(below) bull funds (green) and bear / inverse funds (red), last {len(rows)} days.</p>
<svg viewBox="0 0 {w} {h}" width="100%" style="max-width:{w}px"><line x1="0" x2="{w}" y1="{h / 2}" y2="{h / 2}" stroke="#334155"/>{"".join(bars)}</svg>
<h2>Forced rebalance at today's close (by underlying)</h2><p>Leveraged funds must trade this in the underlying near 16:00 to reset leverage: buy after up days, sell after down days.</p>
<table>{reb or '<tr><td>no data</td></tr>'}</table><p style="color:#64748b">Descriptor, not a signal.</p></body></html>"""


def main() -> None:
    a = argparse.ArgumentParser()
    a.add_argument("--no-telegram", action="store_true")
    args = a.parse_args()
    from sqlalchemy import select

    from trading_intel.config import get_settings
    from trading_intel.memory.db import make_session_factory
    from trading_intel.memory.models import LetfSharesSnapshot
    s = get_settings()
    by: dict[str, list[SharesRow]] = defaultdict(list)
    with make_session_factory(s)() as session:
        for r in session.execute(select(LetfSharesSnapshot).where(
                LetfSharesSnapshot.ts >= date.today() - timedelta(days=45))).scalars():
            by[r.symbol].append(SharesRow(ts=r.ts, shares_outstanding=int(r.shares_outstanding), nav=r.nav))
    sm = summarize(by)
    out = Path("reports/letf")
    out.mkdir(parents=True, exist_ok=True)
    f = out / f"letf_{date.today():%Y%m%d}.html"
    f.write_text(render(sm), encoding="utf-8")
    print("wrote", f)
    if not args.no_telegram and sm["days"]:
        from trading_intel.clients.telegram import TelegramClient
        d, b, r = sm["days"][-1]
        TelegramClient(s).send_message(f"LETF flows {d}: bull funds {b / 1e6:+,.0f}M, bear funds {r / 1e6:+,.0f}M net creations.")
        TelegramClient(s).send_document(f, caption="Leveraged ETF flows")


if __name__ == "__main__":
    main()
