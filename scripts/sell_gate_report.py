"""
sell_gate_report.py - created 2026-11-22. The 0DTE Sell Gate (Imran Lakha's 4 stop signs) as a phone page.

Top: tomorrow's gate (0 = sell as normal, 1-2 = half size / wider wings, 3-4 = no premium selling), each sign
in plain words. Below: the seven locked daily tests (nightly replay, reports/replay/<day>/seven_tests.json)
split by that day's gate bucket, so we can see whether the gate would have saved money. The gate is a TAG only;
the seven tests' parameters stay frozen. Every number is written into the HTML (no scripts) for Telegram.

Run nightly after cvx replay (DSM "cvx replay" 22:15 -> append ; bash .../run_script.sh sell_gate_report --telegram).
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TEST_NAMES = ["1 Fly @ vanna top", "2 Fly @ gamma zero", "3 Fly @ vanna bottom", "4 Fly @ gamma zero +1SD",
              "5 Credit spread", "6 Debit 1:5", "7 Old fly"]
BUCKETS = ("sell", "caution", "stop", "unknown")
BCOL = {"sell": "#0f7b55", "caution": "#b7791f", "stop": "#b42318", "unknown": "#5b6675"}
BLABEL = {"sell": "0 signs", "caution": "1–2 signs", "stop": "3–4 signs", "unknown": "no read"}


def money(v: float) -> str:
    return ("+$" if v >= 0 else "−$") + format(abs(round(v)), ",")


def next_session(d: date) -> date:
    from trading_intel.timeutils import is_trading_session

    n = d + timedelta(days=1)
    while not is_trading_session(n):
        n += timedelta(days=1)
    return n


def split_by_gate(replay_root: Path, gates: dict[str, str], basis: str) -> dict:
    """{bucket: {test: [n, risk, pl]}, '_days': {bucket: [days]}} from every nightly seven_tests.json."""
    acc = {b: {t: [0, 0, 0] for t in TEST_NAMES} for b in BUCKETS}
    days: dict[str, list[str]] = {b: [] for b in BUCKETS}
    for p in sorted(replay_root.glob("2*/seven_tests.json")):
        d = p.parent.name
        d_iso = "%s-%s-%s" % (d[:4], d[4:6], d[6:8])
        try:
            tj = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        b = gates.get(d_iso, "unknown")
        days[b].append(d_iso)
        for s in (tj.get(basis) or {}).get("summary", []):
            a = acc[b].get(s["test"])
            if a:
                a[0] += s["n"]; a[1] += s["risk"]; a[2] += s["pl"]
    acc["_days"] = days
    return acc


def page(nxt: dict, split: dict, basis: str) -> str:
    from trading_intel.market.sell_gate import BUCKET_PLAIN, PLAIN

    e = html.escape
    b = nxt["bucket"]
    h = ["<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
         "<title>0DTE Sell Gate</title><style>body{font:18px/1.45 system-ui,sans-serif;margin:12px;color:#14181f;background:#fff}"
         "h1{font-size:26px;margin:4px 0}h2{font-size:21px;margin:20px 0 6px}.big{font-size:30px;font-weight:700;color:#fff;"
         "border-radius:10px;padding:12px 14px}.row{display:flex;justify-content:space-between;gap:8px;border-bottom:1px solid #d9dee5;"
         "padding:8px 0}.on{color:#b42318;font-weight:700}.off{color:#0f7b55;font-weight:700}.na{color:#5b6675}"
         "table{border-collapse:collapse;width:100%;font-size:16px}td,th{padding:6px 4px;border-bottom:1px solid #d9dee5;text-align:right}"
         "td:first-child,th:first-child{text-align:left}.note{color:#3d4652;font-size:16px}</style></head><body>"]
    h.append("<h1>0DTE Sell Gate — %s</h1>" % e(nxt["day"]))
    h.append("<div class='big' style='background:%s'>%s</div>" % (BCOL[b], e(BUCKET_PLAIN[b])))
    h.append("<p class='note'>Imran Lakha's 4 stop signs before selling 0DTE premium, read from the last close.</p>")
    for k, v in nxt["signs"].items():
        cls, word = ("na", "no data") if v is None else (("on", "ON") if v else ("off", "off"))
        h.append("<div class='row'><span>%s</span><span class='%s'>%s</span></div>" % (e(PLAIN[k]), cls, word))
    vals = nxt["values"]
    bits = []
    if vals.get("vix") is not None:
        bits.append("VIX %.1f" % vals["vix"])
    if vals.get("skew_pctile") is not None:
        bits.append("skew at %d%% of its year" % round(vals["skew_pctile"] * 100))
    if vals.get("front_vol_now") is not None:
        bits.append("7-day vol %.1f" % (vals["front_vol_now"] * 100))
    if bits:
        h.append("<p class='note'>%s</p>" % e(" · ".join(bits)))
    h.append("<h2>Would the gate have helped? (seven daily tests, %s basis)</h2>" % e(basis))
    h.append("<p class='note'>Each replayed day is tagged by its own gate. If the gate works, the 3–4 sign days should "
             "lose money on the selling tests (1–5, 7) and the 0 sign days should make it.</p>")
    h.append("<table><tr><th>test</th>" + "".join("<th style='color:%s'>%s</th>" % (BCOL[x], BLABEL[x]) for x in BUCKETS[:3]) + "</tr>")
    for t in TEST_NAMES:
        cells = []
        for x in BUCKETS[:3]:
            n, _r, pl = split[x][t]
            cells.append("<td>%s<br><span class='note'>%d trades</span></td>" % (money(pl), n) if n else "<td class='na'>–</td>")
        h.append("<tr><td>%s</td>%s</tr>" % (e(t), "".join(cells)))
    h.append("<tr><td><b>days</b></td>%s</tr></table>" % "".join("<td>%d</td>" % len(split["_days"][x]) for x in BUCKETS[:3]))
    if split["_days"]["unknown"]:
        h.append("<p class='note'>%d replay days had too little data for a read (left out).</p>" % len(split["_days"]["unknown"]))
    h.append("<p class='note'>v1: sign 2 uses 7-day vol at a fixed delta as a stand-in for fixed-strike vol. "
             "The gate only tags days; the seven tests' settings stay frozen. Not investment advice.</p></body></html>")
    return "".join(h)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", default=str(ROOT / "reports" / "replay"))
    ap.add_argument("--basis", default="oi")
    ap.add_argument("--out", default=str(ROOT / "reports" / "sell_gate.html"))
    ap.add_argument("--telegram", action="store_true")
    a = ap.parse_args()

    from trading_intel.config import get_settings
    from trading_intel.market.sell_gate import gate_for_session
    from trading_intel.memory.db import make_session_factory
    from trading_intel.timeutils import eastern_today

    st = get_settings()
    root = Path(a.replay)
    with make_session_factory(st)() as s:
        nxt = gate_for_session(s, next_session(eastern_today()))
        gates = {}
        for p in root.glob("2*/seven_tests.json"):
            d = p.parent.name
            day = date(int(d[:4]), int(d[4:6]), int(d[6:8]))
            gates[day.isoformat()] = gate_for_session(s, day)["bucket"]
    split = split_by_gate(root, gates, a.basis)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page(nxt, split, a.basis), encoding="utf-8", newline="\n")
    print("sell_gate", nxt["day"], nxt["bucket"], nxt["count"], "->", out)
    if a.telegram:
        from trading_intel.clients.telegram import TelegramClient

        cap = "0DTE Sell Gate %s: %s" % (nxt["day"], {"sell": "SELL OK", "caution": "CAUTION", "stop": "STOP - no premium selling",
                                                       "unknown": "no read"}[nxt["bucket"]])
        print("telegram_sent=", TelegramClient(st).send_document(out, caption=cap))


if __name__ == "__main__":
    main()
