"""spx_put_buy_ratio_report.py - created 2026-11-04 (Phase 2, item #0). Zero Convex calls.

Are customers BUYING or SELLING SPX out-of-the-money puts, and is that changing?
  (a) Convex split  : pos_days (the position ledger's daily rows from the cockpit collector) -
                      Convex's measured buy vs sell volume and premium on every SPX put strike
                      below the close, 0-11 DTE. Complete (every trade), but mid prints carry
                      Convex's own guess at the side.
  (b) Tape, quote-checked : tas_prints SPX/SPXW OTM puts from $5k that traded AT the bid or AT
                      the ask of their own quote (migration 0052); spread legs and deep-ITM
                      financing dropped. Sample of the tape, but every side is solid.
  (c) agreement of (a) and (b), and each ratio as a z-score vs our OWN trailing 20 sessions
      (not a published baseline), next to the 25-delta 30-day risk reversal from index_skew_daily.
Read (descriptor, rule 4): put buying falling while skew rises = hedges being unwound into strength;
put buying rising with skew rising = demand for protection. Not a signal.
Not yet: the agreement with the overnight OI + IV rule - SPX has no per-strike IV history banked.

Run (NAS DSM, Mon-Fri 17:45 after the 17:30 pos_ledger build):
    bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_script.sh spx_put_buy_ratio_report
Options: --date YYYY-MM-DD  --no-telegram
Outputs: reports/put_buy_ratio/put_buy_ratio_YYYYMMDD.html, reports/put_buy_ratio/history.csv
"""
from __future__ import annotations

import argparse
import csv
import statistics
import sys
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

OUT = HERE.parent / "reports" / "put_buy_ratio"
LOOKBACK = 20
MIN_HIST = 10


def log(msg: str) -> None:
    print(f"{datetime.now():%H:%M:%S}  {msg}", flush=True)


def _sess(settings):
    from trading_intel.memory.db import make_session_factory

    return make_session_factory(settings)()


def load(settings, upto: date) -> dict:
    """{day: {"spot", "cvx_vol_buy", "cvx_vol_sell", "cvx_prem_buy", "cvx_prem_sell", "tape_buy", "tape_sell", "tape_n", "rr"}}"""
    from sqlalchemy import text

    days: dict = {}
    with _sess(settings) as s:
        spot = {r[0]: r[1] for r in s.execute(text(
            "select ts, spx_close from breadth_snapshots where source = 'fmp_sp500' and spx_close is not null "
            "and ts <= :d order by ts desc limit 80"), {"d": upto})}
        rr = {r[0]: r[1] for r in s.execute(text(
            "select date, spx_rr_25d_30d from index_skew_daily where date <= :d order by date desc limit 80"), {"d": upto})}
        for d, S in spot.items():
            days[d] = {"spot": float(S), "rr": rr.get(d)}
        lo = min(days) if days else upto
        for r in s.execute(text(
                "select trade_date, strike, vb, vs, val_b, val_s from pos_days where symbol = 'SPX' and \"right\" = 'P' "
                "and trade_date between :lo and :hi"), {"lo": lo, "hi": upto}):
            d = days.get(r[0])
            if not d or r[1] is None or r[1] >= d["spot"]:
                continue
            d["cvx_vol_buy"] = d.get("cvx_vol_buy", 0) + (r[2] or 0)
            d["cvx_vol_sell"] = d.get("cvx_vol_sell", 0) + (r[3] or 0)
            d["cvx_prem_buy"] = d.get("cvx_prem_buy", 0) + (r[4] or 0)
            d["cvx_prem_sell"] = d.get("cvx_prem_sell", 0) + (r[5] or 0)
        try:
            for r in s.execute(text(
                    "select trade_date, strike, quote_loc, notional from tas_prints where root in ('SPX', 'SPXW') "
                    "and cp = 'P' and quote_loc in ('at_ask', 'at_bid') and leg_group is null "
                    "and coalesce(spread_leg, false) = false and coalesce(is_financing, false) = false "
                    "and trade_date between :lo and :hi"), {"lo": lo, "hi": upto}):
                d = days.get(r[0])
                if not d or r[1] is None or r[1] >= d["spot"]:
                    continue
                k = "tape_buy" if r[2] == "at_ask" else "tape_sell"
                d[k] = d.get(k, 0) + (r[3] or 0)
                d["tape_n"] = d.get("tape_n", 0) + 1
        except Exception as exc:  # noqa: BLE001 - quote_loc column missing before migration 0052
            log(f"tape read skipped: {str(exc)[:120]}")
    return days


def ratio(b, s):
    return b / (b + s) if b is not None and s is not None and (b + s) > 0 else None


def z(v, hist):
    h = [x for x in hist if x is not None]
    if v is None or len(h) < MIN_HIST:
        return None
    sd = statistics.pstdev(h)
    return (v - statistics.mean(h)) / sd if sd > 0 else None


def build(days: dict) -> list[dict]:
    rows = []
    for d in sorted(days):
        x = days[d]
        rows.append({"date": d.isoformat(), "spot": x["spot"], "rr": x.get("rr"),
                     "cvx_vol": ratio(x.get("cvx_vol_buy"), x.get("cvx_vol_sell")),
                     "cvx_prem": ratio(x.get("cvx_prem_buy"), x.get("cvx_prem_sell")),
                     "cvx_prem_total": (x.get("cvx_prem_buy") or 0) + (x.get("cvx_prem_sell") or 0),
                     "tape": ratio(x.get("tape_buy"), x.get("tape_sell")), "tape_n": x.get("tape_n", 0),
                     "tape_prem": (x.get("tape_buy") or 0) + (x.get("tape_sell") or 0)})
    for i, r in enumerate(rows):
        prev = rows[max(0, i - LOOKBACK):i]
        r["z_cvx"] = z(r["cvx_prem"], [p["cvx_prem"] for p in prev])
        r["z_tape"] = z(r["tape"], [p["tape"] for p in prev])
        r["d_rr"] = (r["rr"] - prev[-1]["rr"]) if prev and r["rr"] is not None and prev[-1]["rr"] is not None else None
        r["agree"] = (None if r["cvx_prem"] is None or r["tape"] is None
                      else ((r["cvx_prem"] - 0.5) * (r["tape"] - 0.5) > 0))
    return rows


def read(r) -> str:
    zc, dr = r.get("z_cvx"), r.get("d_rr")
    if zc is None:
        return "Not enough history yet (needs %d sessions of our own data)." % MIN_HIST
    lvl = "much more" if zc >= 1.5 else "more" if zc >= 0.5 else "much less" if zc <= -1.5 else "less" if zc <= -0.5 else "about the usual share of"
    s = "Customers bought %s OTM-put premium than usual (z %+.1f)." % (lvl, zc)
    if dr is not None:
        # spx_rr_25d_30d = 25d put IV minus 25d call IV (vol/skew.py): rising = put skew steeper
        steeper = dr > 0
        if zc <= -0.5 and steeper:
            s += " Put skew steepened while put buying fell: protection is being sold or unwound into the move, not bought."
        elif zc >= 0.5 and steeper:
            s += " Put skew steepened with the buying: real demand for protection."
        elif zc >= 0.5 and not steeper:
            s += " Buying rose but skew flattened: buyers are absorbing supply, demand is not pushing price."
        else:
            s += " Skew flattened with less buying: hedges being taken off."
    return s


def pct(v):
    return "–" if v is None else "%.0f%%" % (100 * v)


def html(rows: list[dict], day: str) -> str:
    r = rows[-1]
    css = ("body{background:#0d1117;color:#e6edf3;font:18px/1.55 system-ui,Segoe UI,Arial,sans-serif;margin:0;padding:24px 16px}"
           "main{max-width:1100px;margin:0 auto}h1{font-size:30px;margin:0 0 6px}h2{font-size:23px;margin:28px 0 8px}"
           ".card{background:#151b23;border:1px solid #2b333d;border-radius:10px;padding:14px 18px;margin:10px 0;font-size:20px}"
           "table{border-collapse:collapse;width:100%;font-size:17px}th,td{border-bottom:1px solid #2b333d;padding:6px 8px;text-align:right}"
           "th:first-child,td:first-child{text-align:left}th{color:#c9d4e3}.sub{color:#c9d4e3;font-size:17px}")
    h = ["<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
         f"<title>SPX Put Buy Ratio {day}</title><style>{css}</style></head><body><main>",
         f"<h1>SPX out-of-the-money put buying · {day}</h1>",
         "<div class='sub'>Share of OTM put premium that customers BOUGHT (50% = balanced). Zero vendor calls. Descriptive, not a signal.</div>",
         f"<div class='card'>{read(r)}</div>",
         "<table><tr><th>measure</th><th>today</th><th>vs own 20 days</th><th>what it is</th></tr>",
         f"<tr><td>Convex split (premium)</td><td>{pct(r['cvx_prem'])}</td><td>{'–' if r['z_cvx'] is None else '%+.1f' % r['z_cvx']}</td>"
         "<td style='text-align:left'>Every SPX OTM put trade 0–11 DTE; Convex's buy/sell split (its guess on mid prints).</td></tr>",
         f"<tr><td>Convex split (contracts)</td><td>{pct(r['cvx_vol'])}</td><td></td><td style='text-align:left'>Same, counted in contracts.</td></tr>",
         f"<tr><td>Tape, quote-checked</td><td>{pct(r['tape'])}</td><td>{'–' if r['z_tape'] is None else '%+.1f' % r['z_tape']}</td>"
         f"<td style='text-align:left'>{r['tape_n']} prints from $5k that traded AT the bid or AT the ask; spreads and financing removed.</td></tr>",
         f"<tr><td>Do they agree?</td><td>{'–' if r['agree'] is None else ('yes' if r['agree'] else 'NO')}</td><td></td>"
         "<td style='text-align:left'>Both on the same side of 50%. A NO means mid-price guesses are driving the Convex number.</td></tr>",
         f"<tr><td>25Δ 30-day risk reversal</td><td>{'–' if r['rr'] is None else '%.2f' % r['rr']}</td><td>{'–' if r['d_rr'] is None else 'Δ %+.2f' % r['d_rr']}</td>"
         "<td style='text-align:left'>Put IV minus call IV. Rising = put skew getting steeper.</td></tr></table>",
         "<h2>History</h2><table><tr><th>date</th><th>SPX</th><th>Convex split</th><th>z</th><th>tape</th><th>prints</th><th>agree</th><th>RR</th></tr>"]
    for x in reversed(rows[-30:]):
        h.append(f"<tr><td>{x['date']}</td><td>{x['spot']:,.0f}</td><td>{pct(x['cvx_prem'])}</td><td>{'–' if x['z_cvx'] is None else '%+.1f' % x['z_cvx']}</td>"
                 f"<td>{pct(x['tape'])}</td><td>{x['tape_n']}</td><td>{'–' if x['agree'] is None else ('yes' if x['agree'] else 'no')}</td>"
                 f"<td>{'–' if x['rr'] is None else '%.2f' % x['rr']}</td></tr>")
    h.append("</table></main></body></html>")
    return "".join(h)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--date")
    ap.add_argument("--no-telegram", action="store_true")
    a = ap.parse_args(argv)
    from trading_intel.config import get_settings

    settings = get_settings()
    day = date.fromisoformat(a.date) if a.date else datetime.now().date()
    days = load(settings, day)
    rows = [r for r in build(days) if r["cvx_prem"] is not None or r["tape"] is not None]
    if not rows or rows[-1]["date"] != day.isoformat():
        log(f"no SPX put data for {day} (pos_days / tas_prints) - nothing written")
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    fp = OUT / f"put_buy_ratio_{day:%Y%m%d}.html"
    fp.write_text(html(rows, day.isoformat()), encoding="utf-8")
    cols = ["date", "spot", "cvx_prem", "cvx_vol", "cvx_prem_total", "z_cvx", "tape", "tape_n", "tape_prem", "z_tape", "agree", "rr", "d_rr"]
    with open(OUT / "history.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    r = rows[-1]
    msg = (f"<b>SPX OTM put buying {day:%a %d %b}</b>\nConvex split {pct(r['cvx_prem'])}"
           f"{'' if r['z_cvx'] is None else ' (z %+.1f)' % r['z_cvx']} · tape quote-checked {pct(r['tape'])} ({r['tape_n']} prints)\n{read(r)}")
    log(msg.replace("<b>", "").replace("</b>", ""))
    if not a.no_telegram:
        try:
            from trading_intel.clients.telegram import TelegramClient

            tc = TelegramClient(settings)
            if tc.enabled:
                tc.send_message(msg)
                tc.send_document(fp, caption=f"SPX put buy ratio {day}")
        except Exception as exc:  # noqa: BLE001
            log(f"telegram failed: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
