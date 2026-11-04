"""Swing gates backtest (Phase 5 P6, created 2026-11-04).

Question: when the price-only gates pass (gate 1 trend + gate 2 momentum, in the
lean's direction), does the stock follow through better than on any leaned day?

Data: ``quotes_daily`` closes (free, already banked — no vendor calls) for every
ticker in ``swing_features``, last ``--years`` years. The IV / skew / dealer gates
(3-7) only exist since the features started banking (2026-07), so they CANNOT be
backtested yet — this tests the part that can be.

Method:
  * every session, rebuild the lean + gates 1-2 from closes (``gates.price_only_gates``)
  * outcome = lean-signed forward return over 20 and 40 sessions
  * non-overlapping sample: one observation per ticker every 20 sessions
  * time split: the last 30% of dates are a holdout never used to judge the design
PASS (on the holdout, 20-session horizon, non-overlapping): gated hit-rate beats
the leaned-day base rate by >= 5 points AND the gated mean return beats the base
mean, with >= 150 gated observations. PASS -> the P4 generator's ``experimental``
flag can be dropped; FAIL -> keep it. Descriptor only (rule 4).

Run (Windows, venv):
    .venv\\Scripts\\python scripts\\swing_backtest.py
    .venv\\Scripts\\python scripts\\swing_backtest.py --years 2
"""

from __future__ import annotations

import argparse
import html
import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, text
from trading_intel.config import get_settings

from trading_intel.swing.gates import price_only_gates

_REPO_ROOT = Path(__file__).resolve().parents[1]
_OUT = _REPO_ROOT / "reports"
HORIZONS = (20, 40)
STEP = 20
HOLDOUT = 0.30
MIN_N = 150
EDGE_PTS = 0.05


def fetch(years: int, *, settings=None) -> dict[str, list[tuple[date, float]]]:
    eng = create_engine((settings or get_settings()).DATABASE_URL)
    since = date.today() - timedelta(days=int(365.25 * years) + 120)
    with eng.connect() as cx:
        syms = [r[0] for r in cx.execute(text("SELECT DISTINCT symbol FROM swing_features")).all()]
        rows = cx.execute(
            text(
                "SELECT symbol, date, close FROM quotes_daily WHERE symbol = ANY(:s) AND date >= :d "
                "ORDER BY symbol, date"
            ),
            {"s": syms, "d": since},
        ).all()
    out: dict[str, list[tuple[date, float]]] = defaultdict(list)
    for s, d, c in rows:
        if c:
            out[s].append((d, float(c)))
    return dict(out)


def observations(series: dict[str, list[tuple[date, float]]]) -> list[dict]:
    obs = []
    for sym, rows in series.items():
        closes = [c for _, c in rows]
        last_taken = -STEP
        for i in range(50, len(rows) - max(HORIZONS)):
            g = price_only_gates(closes, i)
            if g is None or g[0] == "neutral":
                continue
            lean, t_ok, m_ok = g
            sgn = 1.0 if lean == "bullish" else -1.0
            o = {
                "sym": sym,
                "d": rows[i][0],
                "lean": lean,
                "gated": t_ok and m_ok,
                "nonoverlap": i - last_taken >= STEP,
            }
            if o["nonoverlap"]:
                last_taken = i
            for h in HORIZONS:
                o[f"r{h}"] = sgn * (closes[i + h] / closes[i] - 1.0)
            obs.append(o)
    return obs


def _stats(xs: list[dict], h: int) -> dict:
    if not xs:
        return {"n": 0, "hit": None, "mean": None}
    rs = [x[f"r{h}"] for x in xs]
    return {
        "n": len(rs),
        "hit": round(sum(1 for r in rs if r > 0) / len(rs), 4),
        "mean": round(sum(rs) / len(rs), 5),
    }


def evaluate(obs: list[dict]) -> dict:
    if not obs:
        return {"verdict": "NO DATA", "pass": False}
    ds = sorted({o["d"] for o in obs})
    cut = ds[int(len(ds) * (1 - HOLDOUT))]
    res: dict = {
        "cut": cut.isoformat(),
        "first": ds[0].isoformat(),
        "last": ds[-1].isoformat(),
        "tickers": len({o["sym"] for o in obs}),
        "sets": {},
    }
    for split, keep in (("design", lambda o: o["d"] < cut), ("holdout", lambda o: o["d"] >= cut)):
        for sample, kk in (
            ("all_days", lambda o: True),
            ("non_overlap", lambda o: o["nonoverlap"]),
        ):
            base = [o for o in obs if keep(o) and kk(o)]
            gated = [o for o in base if o["gated"]]
            res["sets"][f"{split}/{sample}"] = {
                str(h): {"base": _stats(base, h), "gated": _stats(gated, h)} for h in HORIZONS
            }
    h = res["sets"]["holdout/non_overlap"]["20"]
    b, g = h["base"], h["gated"]
    ok = (
        g["n"] >= MIN_N
        and b["hit"] is not None
        and g["hit"] is not None
        and g["hit"] - b["hit"] >= EDGE_PTS
        and g["mean"] > b["mean"]
    )
    res["pass"] = bool(ok)
    res["verdict"] = "PASS" if ok else "FAIL"
    res["why"] = (
        f"Holdout, 20-session, non-overlapping: gated hit {_p(g['hit'])} vs base {_p(b['hit'])} "
        f"(needs +{EDGE_PTS * 100:.0f} pts), gated mean {_p(g['mean'], 2)} vs base {_p(b['mean'], 2)}, "
        f"n gated = {g['n']} (needs ≥{MIN_N})."
    )
    return res


def _p(x, k=1):
    return "—" if x is None else f"{x * 100:.{k}f}%"


def page(res: dict) -> str:
    rows = []
    for key, by_h in res.get("sets", {}).items():
        for h, v in by_h.items():
            b, g = v["base"], v["gated"]
            rows.append(
                f"<tr><td>{key}</td><td>{h}</td><td>{b['n']:,}</td><td>{_p(b['hit'])}</td><td>{_p(b['mean'], 2)}</td>"
                f"<td>{g['n']:,}</td><td>{_p(g['hit'])}</td><td>{_p(g['mean'], 2)}</td></tr>"
            )
    color = "#4ade80" if res.get("pass") else "#f87171"
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Swing gates backtest</title><style>
body{{background:#0b1220;color:#e6edf6;font-family:system-ui,Segoe UI,Arial,sans-serif;padding:24px;font-size:18px;line-height:1.5}}
h1{{font-size:30px;margin:0 0 6px}}.v{{font-size:28px;font-weight:800;color:{color}}}
table{{border-collapse:collapse;width:100%;margin-top:14px}}th,td{{padding:9px 12px;border-bottom:1px solid #2c3d63;text-align:right}}
th{{background:#1c2a4a}}td:first-child,th:first-child{{text-align:left}}.note{{color:#c8d3e3;font-size:16px;margin-top:18px}}
</style></head><body>
<h1>Swing gates backtest — price-only gates (trend + momentum)</h1>
<p>{res.get('tickers', 0)} tickers · {res.get('first')} → {res.get('last')} · holdout from {res.get('cut')} · generated {datetime.now():%Y-%m-%d %H:%M}</p>
<p class="v">{res.get('verdict')}</p><p>{html.escape(res.get('why', ''))}</p>
<table><tr><th>Set</th><th>Horizon (sessions)</th><th>Leaned days n</th><th>Base hit</th><th>Base mean</th>
<th>Gated n</th><th>Gated hit</th><th>Gated mean</th></tr>{''.join(rows)}</table>
<p class="note"><b>How to read this.</b> "Hit" = share of times the stock moved the way the lean said over the horizon;
"mean" = average lean-signed return. Base = every day with a lean; gated = days where gates 1 and 2 both passed.
The holdout is the newest 30% of dates and is the only part that decides PASS/FAIL. Non-overlapping samples
one day per ticker every 20 sessions so the same move is not counted 20 times. PASS means the P4 generator's
"experimental" tag can come off; the IV, skew and dealer gates are not in this test because their history only
started banking in July 2026.</p></body></html>"""


def build(*, years: int = 2, settings=None) -> tuple[Path, dict]:
    res = evaluate(observations(fetch(years, settings=settings)))
    _OUT.mkdir(parents=True, exist_ok=True)
    path = _OUT / f"swing_backtest_{date.today():%Y-%m-%d}.html"
    path.write_text(page(res), encoding="utf-8")
    path.with_suffix(".json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    print(f"Wrote {path} — {res.get('verdict')}: {res.get('why', '')}")
    return path, res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=2)
    build(years=ap.parse_args().years)


if __name__ == "__main__":
    main()
