#!/usr/bin/env python
# ruff: noqa: E501, RUF100
"""macro_rails.py - 2026-11-13 (overnight run), Claude for Mithil. #60 group: credit/MOVE spark monitor, VIX
futures curve rail and the macro event gate, in one nightly Telegram message + reports/macro_rails/latest.json.

Data (no Convex calls, no new vendor - rule 1):
  FRED (clients/fred.py)               HY OAS BAMLH0A0HYM2; past release dates of CPI (10), jobs (50), GDP (53), PCE (54),
                                       FOMC press release (101)
  FMP via CVForge (clients/fmp_prices) SPY, QQQ, HYG, IEI, ^MOVE, SPX closes
  scripts/cvxmap/data/vixmap_*.json    each VIX expiry's forward from the VIX option chain the collector already pulls
                                       (= the VX future to within cents); kept daily in reports/macro_rails/vx_hist.csv
  scripts/cvxmap/events.json           the hand-kept event calendar (FOMC, CPI, jobs, GDP/PCE, expiries)
Logic: trading_intel/market/macro_rails.py (tested). Descriptors only (rule 4); the event gate's size factor is a
reading aid for short-premium trades, not an order.

RUN (NAS, DSM task "TI macro rails", Mon-Fri 18:10, user root):
    bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_script.sh macro_rails
    ... macro_rails --no-telegram     (JSON only)
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from trading_intel.market import macro_rails as mr  # noqa: E402

DATA = HERE / "cvxmap" / "data"
OUT = REPO / "reports" / "macro_rails"
RELEASES = {"CPI": 10, "Jobs": 50, "GDP": 53, "PCE": 54, "FOMC": 101}
AHEAD_DAYS = 14


def series_tuples(
    df,
) -> list[tuple[date, float]]:  # noqa: ANN001 - pandas frame from FmpPriceSource
    if df is None or len(df) == 0:
        return []
    return [(d.date(), float(c)) for d, c in zip(df["date"], df["close"]) if c == c]


def ratio_series(
    a: list[tuple[date, float]], b: list[tuple[date, float]]
) -> list[tuple[date, float]]:
    bb = dict(b)
    return [(d, x / bb[d]) for d, x in a if bb.get(d)]


def newest_vixmap() -> tuple[date | None, list[dict]]:
    files = sorted(glob.glob(str(DATA / "vixmap_*.json")))
    for f in reversed(files):
        try:
            j = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if j.get("exps"):
            ymd = Path(f).stem.split("_")[-1]
            return date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:])), j["exps"]
    return None, []


def keep_vx(vx: dict) -> list[dict]:
    """Append today's rail to vx_hist.csv (one row per date) and return the last 20 rows."""
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "vx_hist.csv"
    rows = []
    if p.exists():
        with p.open(encoding="utf-8") as fh:
            rows = [r for r in csv.DictReader(fh)]
    if vx.get("found"):
        v = vx["vx"]
        new = {
            "date": vx["asof"],
            "vx1": v[0]["F"],
            "vx2": v[1]["F"],
            "vx3": v[2]["F"] if len(v) > 2 else "",
            "vx2_vx1": vx["vx2_vx1"],
            "vx3_vx1": "" if vx["vx3_vx1"] is None else vx["vx3_vx1"],
            "state": vx["state"],
        }
        rows = [r for r in rows if r.get("date") != new["date"]] + [new]
        rows.sort(key=lambda r: r["date"])
        with p.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(new))
            w.writeheader()
            w.writerows(rows)
    return rows[-20:]


def upcoming_events(today: date) -> list[dict]:
    try:
        ev = (
            json.loads((DATA.parent / "events.json").read_text(encoding="utf-8")).get("events")
            or []
        )
    except (OSError, ValueError):
        return []
    end = today + timedelta(days=AHEAD_DAYS)
    return [e for e in ev if today < date.fromisoformat(e["d"]) <= end]


def build(fred, px) -> dict:  # noqa: ANN001 - FredClient / FmpPriceSource (clients/)
    from trading_intel.clients.fred import HY_OAS_SERIES

    today = date.today()
    out: dict = {"asof": datetime.now().isoformat(timespec="minutes")}
    hy = fred.series(HY_OAS_SERIES)
    hy_t = [(d.date(), float(v)) for d, v in hy.items()][-300:]
    get = {
        s: series_tuples(px.daily_history(s, period="2y"))
        for s in ("SPY", "QQQ", "HYG", "IEI", "^MOVE")
    }
    out["credit"] = mr.credit_spark(
        hy_t, get["SPY"], get["QQQ"], ratio_series(get["HYG"], get["IEI"]), get["^MOVE"]
    )
    d, exps = newest_vixmap()
    out["vx"] = mr.vx_curve(exps, d) if d else {"found": False, "reason": "no vixmap_*.json yet"}
    out["vx_hist"] = keep_vx(out["vx"])
    spx = series_tuples(px.daily_history("SPX", period="5y"))
    hist = {}
    for k, rid in RELEASES.items():
        ds = [x for x in fred.release_dates(rid) if spx and spx[0][0] <= x <= today]
        hist[k] = mr.move_ratio(spx, ds)
    out["event_history"] = hist
    out["events"] = mr.event_gate(upcoming_events(today), hist, out["credit"].get("move_pct_1y"))
    return out


def telegram_text(r: dict) -> str:
    c, v = r.get("credit") or {}, r.get("vx") or {}
    dot = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
    L = ["<b>Macro rails</b> (credit · bond vol · VIX curve · events)"]
    if c.get("found"):
        L.append(
            f"{dot.get(c['state'], '')} Credit: HY OAS {c['hy_oas']:.2f}% ({c['hy_chg_bp']:+.0f} bp)"
            + (
                f" · MOVE {c['move']:.0f} ({c['move_chg1']:+.1f} today, {c['move_chg20']:+.1f} in 20d, {c['move_pct_1y']:.0f}th pct)"
                if c.get("move") is not None
                else ""
            )
        )
        for f in c.get("flags") or []:
            L.append("  • " + f)
    else:
        L.append("Credit: " + str(c.get("reason", "no data")))
    if v.get("found"):
        vv = v["vx"]
        L.append(
            f"{dot.get(v['color'], '')} VIX curve ({v['state']}): "
            + " / ".join(f"VX{i + 1} {x['F']:.2f}" for i, x in enumerate(vv))
            + f" · VX2−VX1 {v['vx2_vx1']:+.2f}"
            + (f" · VX3−VX1 {v['vx3_vx1']:+.2f}" if v.get("vx3_vx1") is not None else "")
            + f" · carry {v['carry_pct_month']:+.1f}%/month (from VIX option forwards)"
        )
    else:
        L.append("VIX curve: " + str(v.get("reason", "no data")))
    ev = r.get("events") or []
    if ev:
        L.append("Events next 2 weeks (size factor for short premium):")
        for e in ev:
            hist = (
                f"{e['kind']} days move {e['ratio']:.1f}× a normal day (n={e['n_past']})"
                if e.get("ratio")
                else f"{e['kind']}: no history"
            )
            L.append(
                f"  {dot.get(e['color'], '')} {e['d'][5:]} {e['t']} {e['label']} — ×{e['size']} · {hist}"
            )
    else:
        L.append("No CPI / jobs / FOMC / GDP / PCE in the next 2 weeks (events.json).")
    L.append("<i>Descriptors, not signals.</i>")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-telegram", action="store_true")
    a = ap.parse_args()
    from trading_intel.clients.cvforge import CVForgeClient
    from trading_intel.clients.fmp_prices import FmpPriceSource
    from trading_intel.clients.fred import FredClient
    from trading_intel.config import get_settings

    s = get_settings()
    r = build(FredClient(s), FmpPriceSource(CVForgeClient(s)))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "latest.json").write_text(json.dumps(r, indent=1, default=str), encoding="utf-8")
    txt = telegram_text(r)
    print(txt.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", ""))
    if not a.no_telegram:
        from trading_intel.clients.telegram import TelegramClient

        TelegramClient(s).send_message(txt)
    print(
        "macro_rails.done credit=%s vx=%s events=%d"
        % ((r["credit"] or {}).get("state"), (r["vx"] or {}).get("state"), len(r["events"]))
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
