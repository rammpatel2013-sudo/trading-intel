"""Expiry-flow term view — one ticker (Phase 5 E1, created 2026-11-04).

Where is the option money going along the term structure? For each days-to-expiry
bucket (0-7 / 8-30 / 31-60 / 60+ days):
  * bullish vs bearish premium from the tape roll-up (``tas_daily_contract``,
    last ``--days`` sessions): calls bought + puts sold = bullish, puts bought +
    calls sold = bearish
  * call vs put open interest (latest EOD chain, ``oi_chain_eod``)
  * who opened the new contracts: OI change paired with each contract's IV change
    (OI up + IV up = buyers; OI up + IV down = writers)
Reads the DB only — zero new Convex calls. Descriptor only (rule 4).

Run (Windows, venv):
    .venv\\Scripts\\python scripts\\expiry_flow_report.py MSFT
    .venv\\Scripts\\python scripts\\expiry_flow_report.py SPX --days 10
"""

from __future__ import annotations

import argparse
import html
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import create_engine, text
from trading_intel.config import get_settings

from trading_intel.flow import expiry_flow as ef
from trading_intel.timeutils import live_expiry_floor

_REPO_ROOT = Path(__file__).resolve().parents[1]
_OUT = _REPO_ROOT / "reports"
ROOTS = {"SPX": ("SPX", "SPXW"), "NDX": ("NDX", "NDXP"), "RUT": ("RUT", "RUTW")}
GRN, RED, NEU = "#4ade80", "#f87171", "#e6edf6"

_STYLE = """
body{background:#0b1220;color:#e6edf6;font-family:system-ui,Segoe UI,Arial,sans-serif;margin:0;padding:24px;font-size:18px;line-height:1.5}
h1{font-size:30px;margin:0 0 6px}h2{font-size:24px;margin:28px 0 10px}
.sub{color:#c8d3e3;font-size:17px;margin:0 0 18px}
.read{background:#14203a;border:1px solid #2c3d63;border-radius:10px;padding:14px 18px;margin:12px 0}
.read li{margin:6px 0}
table{border-collapse:collapse;width:100%;font-size:18px;margin:6px 0 10px}
th,td{padding:9px 12px;text-align:right;border-bottom:1px solid #2c3d63}
th{background:#1c2a4a;color:#fff}td:first-child,th:first-child{text-align:left;font-weight:700}
.bar{display:inline-block;height:14px;vertical-align:middle;border-radius:3px}
.note{color:#c8d3e3;font-size:16px;margin-top:18px;line-height:1.6}
"""


def _m(x: float) -> str:
    a = abs(x)
    s = f"${a / 1e9:.2f}bn" if a >= 1e9 else f"${a / 1e6:.1f}m" if a >= 1e6 else f"${a / 1e3:.0f}k"
    return ("−" if x < 0 else "") + s


def fetch(symbol: str, days: int, *, settings=None) -> dict:
    sym = symbol.strip().upper()
    roots = ROOTS.get(sym, (sym,))
    eng = create_engine((settings or get_settings()).DATABASE_URL)
    with eng.connect() as cx:
        tds = [
            r[0]
            for r in cx.execute(
                text(
                    "SELECT DISTINCT trade_date FROM tas_daily_contract WHERE root = ANY(:r) "
                    "ORDER BY trade_date DESC LIMIT :n"
                ),
                {"r": list(roots), "n": days},
            ).all()
        ]
        tape = []
        if tds:
            tape = cx.execute(
                text(
                    "SELECT trade_date, expiry, cp, buy_notional, sell_notional FROM tas_daily_contract "
                    "WHERE root = ANY(:r) AND trade_date >= :d0"
                ),
                {"r": list(roots), "d0": min(tds)},
            ).all()
        ts = [
            r[0]
            for r in cx.execute(
                text(
                    "SELECT DISTINCT ts FROM oi_chain_eod WHERE symbol = :s ORDER BY ts DESC LIMIT 2"
                ),
                {"s": sym},
            ).all()
        ]
        chain = []
        if ts:
            t, pt = ts[0], (ts[1] if len(ts) > 1 else None)
            floor = live_expiry_floor(t.date() if isinstance(t, datetime) else t)
            chain = cx.execute(
                text(
                    "SELECT c.expiry, c.cp, c.oi, c.oi_change, c.iv, p.iv "
                    "FROM oi_chain_eod c LEFT JOIN oi_chain_eod p ON p.symbol = c.symbol AND p.ts = :pt "
                    "AND p.expiry = c.expiry AND p.strike = c.strike AND p.cp = c.cp "
                    "WHERE c.symbol = :s AND c.ts = :t AND c.expiry >= :f"
                ),
                {"s": sym, "t": t, "pt": pt, "f": floor},
            ).all()
    book_day = ts[0].date() if ts and isinstance(ts[0], datetime) else (ts[0] if ts else None)
    return {
        "symbol": sym,
        "roots": roots,
        "trade_dates": sorted(tds),
        "tape": tape,
        "chain": chain,
        "book_ts": ts[0] if ts else None,
        "prev_ts": ts[1] if len(ts) > 1 else None,
        "book_day": book_day,
    }


def compute(raw: dict) -> dict:
    bd = raw["book_day"]
    prem = ef.premium_by_bucket(raw["tape"])
    oi = (
        ef.oi_by_bucket([((r[0] - bd).days, r[1], r[2]) for r in raw["chain"]])
        if bd
        else ef.oi_by_bucket([])
    )
    dw = (
        ef.demand_write([((r[0] - bd).days, r[1], r[3], r[4], r[5]) for r in raw["chain"]])
        if bd
        else ef.demand_write([])
    )
    return {"premium": prem, "oi": oi, "demand_write": dw, "read": ef.plain_read(prem, dw)}


def _bar(v: float, mx: float, color: str) -> str:
    w = 0 if not mx else max(2, int(160 * abs(v) / mx))
    return f'<span class="bar" style="width:{w}px;background:{color}"></span>'


def page(raw: dict, res: dict) -> str:
    sym = html.escape(raw["symbol"])
    tds = raw["trade_dates"]
    span = f"{tds[0]} → {tds[-1]} ({len(tds)} sessions)" if tds else "no tape rows"
    prem, oi, dw = res["premium"], res["oi"], res["demand_write"]
    mx = max([abs(prem[b]["bull"]) for b in prem] + [abs(prem[b]["bear"]) for b in prem] + [1.0])

    def _share(x):
        return "" if x is None else f"{x * 100:.0f}%"

    rows_p = "".join(
        f"<tr><td>{b}</td><td>{_m(p['bull'])} {_bar(p['bull'], mx, GRN)}</td>"
        f"<td>{_m(p['bear'])} {_bar(p['bear'], mx, RED)}</td>"
        f"<td style='color:{GRN if p['net'] >= 0 else RED}'>{_m(p['net'])}</td>"
        f"<td>{_share(p['bull_share'])}</td></tr>"
        for b, p in prem.items()
    )
    rows_o = "".join(
        f"<tr><td>{b}</td><td>{o['call_oi']:,}</td><td>{o['put_oi']:,}</td>"
        f"<td>{'' if o['pc_ratio'] is None else o['pc_ratio']}</td></tr>"
        for b, o in oi.items()
    )

    def sh(x):
        return "—" if x is None else f"{x * 100:.0f}%"

    rows_d = "".join(
        f"<tr><td>{b}</td><td>{d['call_bought_open']:,}</td><td>{d['call_written_open']:,}</td>"
        f"<td>{sh(d['call_demand_share'])}</td><td>{d['put_bought_open']:,}</td><td>{d['put_written_open']:,}</td>"
        f"<td>{sh(d['put_demand_share'])}</td><td>{d['call_long_close'] + d['put_long_close']:,}</td>"
        f"<td>{d['call_short_cover'] + d['put_short_cover']:,}</td></tr>"
        for b, d in dw.items()
    )
    read = (
        "".join(f"<li>{html.escape(x)}</li>" for x in res["read"])
        or "<li>No flow in the window.</li>"
    )
    book = raw["book_ts"]
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{sym} expiry flow</title><style>{_STYLE}</style></head><body>
<h1>{sym} — where the option money is going, by expiry</h1>
<p class="sub">Tape: {html.escape(span)} · chain: {html.escape(str(book))} vs {html.escape(str(raw['prev_ts']))} · generated {datetime.now():%Y-%m-%d %H:%M}</p>
<div class="read"><b>Plain read</b><ul>{read}</ul></div>
<h2>1 · Premium by expiry bucket</h2>
<table><tr><th>Bucket</th><th>Bullish $ (calls bought + puts sold)</th><th>Bearish $ (puts bought + calls sold)</th><th>Net</th><th>Bullish share</th></tr>{rows_p}</table>
<h2>2 · Open interest by expiry bucket</h2>
<table><tr><th>Bucket</th><th>Call OI</th><th>Put OI</th><th>Put/Call</th></tr>{rows_o}</table>
<h2>3 · Who opened the new contracts (OI change × IV change)</h2>
<table><tr><th>Bucket</th><th>Calls opened by buyers</th><th>Calls opened by writers</th><th>Call demand share</th>
<th>Puts opened by buyers</th><th>Puts opened by writers</th><th>Put demand share</th><th>Longs closing</th><th>Shorts covering</th></tr>{rows_d}</table>
<p class="note"><b>How to read this.</b> Section 1 is dollars that traded on the tape, split by which side the
aggressor took: a bought call or a sold put makes money if the stock rises, so both count as bullish.
Section 2 is the open interest sitting in each bucket now. Section 3 answers what open interest alone cannot —
whether a new position was opened by a buyer or a writer: if open interest rose AND the contract's implied vol
rose, buyers paid up to open it (demand-led); if open interest rose while IV fell, writers sold it to open
(supply-led). Units are contracts. Demand share above 50% = buyers dominated new positions in that bucket.
Descriptive only — not a signal.</p>
</body></html>"""


def build(
    symbol: str, *, days: int = 5, settings=None, out: Path | None = None
) -> tuple[Path, dict]:
    raw = fetch(symbol, days, settings=settings)
    res = compute(raw)
    path = Path(out) if out else _OUT / f"expiry_flow_{raw['symbol']}_{date.today():%Y-%m-%d}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page(raw, res), encoding="utf-8")
    summary = {
        "symbol": raw["symbol"],
        "trade_dates": [d.isoformat() for d in raw["trade_dates"]],
        "book_ts": str(raw["book_ts"]),
        "read": res["read"],
        "premium": res["premium"],
        "oi": res["oi"],
        "demand_write": res["demand_write"],
    }
    print(f"Wrote {path}")
    return path, summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol")
    ap.add_argument("--days", type=int, default=5)
    a = ap.parse_args()
    build(a.symbol, days=a.days)


if __name__ == "__main__":
    main()
