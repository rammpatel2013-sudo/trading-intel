"""Swing scorecard — 9-gate card per ticker (Phase 5 P5, created 2026-11-04).

Reads the banked ``swing_features`` rows (newest per ticker) + ``earnings_events``
and writes ``reports/swing_scorecard_<date>.html``: one card per ticker, nine
gates green/red/grey, flagged when >= 6 of 9 pass. DB only — no vendor calls.
Gate definitions: ``trading_intel/swing/gates.py``. Descriptor only (rule 4).

Run (Windows, venv):
    .venv\\Scripts\\python scripts\\swing_scorecard.py              # every banked ticker
    .venv\\Scripts\\python scripts\\swing_scorecard.py NVDA MSFT AAPL
"""

from __future__ import annotations

import argparse
import html
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import create_engine, text
from trading_intel.config import get_settings

from trading_intel.swing import gates as sg

_REPO_ROOT = Path(__file__).resolve().parents[1]
_OUT = _REPO_ROOT / "reports"
COLS = (
    "spot",
    "atm_iv",
    "rv20",
    "iv_rv",
    "rsi14",
    "sma50",
    "px_vs_sma50",
    "skew_25d",
    "gex",
    "dex",
    "atm_iv_rank_252d",
    "iv_rv_pctile_252d",
    "skew_pctile_252d",
    "gex_pctile_252d",
    "dex_pctile_252d",
)

_STYLE = """
body{background:#0b1220;color:#e6edf6;font-family:system-ui,Segoe UI,Arial,sans-serif;margin:0;padding:24px;font-size:18px;line-height:1.45}
h1{font-size:30px;margin:0 0 6px}.sub{color:#c8d3e3;font-size:17px;margin:0 0 18px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(380px,1fr));gap:16px}
.card{background:#14203a;border:2px solid #2c3d63;border-radius:12px;padding:14px 16px}
.card.flag{border-color:#4ade80}
.top{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}
.sym{font-size:26px;font-weight:800}.lean{font-size:18px;font-weight:700}
.cnt{margin-left:auto;font-size:22px;font-weight:800}
.badge{background:#4ade80;color:#06210f;border-radius:6px;padding:2px 8px;font-size:15px;font-weight:800}
.struct{color:#e6edf6;font-size:16px;margin:4px 0 8px}
.g{display:grid;grid-template-columns:24px 1fr auto;gap:2px 8px;font-size:16px;padding:3px 0;border-top:1px solid #22314f}
.g .v{font-weight:700;text-align:right}.g .w{grid-column:2/4;color:#c8d3e3;font-size:15px}
.ok{color:#4ade80}.no{color:#f87171}.na{color:#9fb0c8}
.note{color:#c8d3e3;font-size:16px;margin-top:20px;line-height:1.6}
"""


def fetch(symbols: list[str] | None, *, settings=None) -> tuple[list[dict], dict[str, date]]:
    eng = create_engine((settings or get_settings()).DATABASE_URL)
    with eng.connect() as cx:
        q = (
            "SELECT DISTINCT ON (symbol) symbol, ts, "
            + ", ".join(COLS)
            + " FROM swing_features"
            + (" WHERE symbol = ANY(:s)" if symbols else "")
            + " ORDER BY symbol, ts DESC"
        )
        feats = [
            dict(r) for r in cx.execute(text(q), {"s": symbols} if symbols else {}).mappings().all()
        ]
        earn = {
            r[0]: r[1]
            for r in cx.execute(
                text(
                    "SELECT symbol, min(date) FROM earnings_events WHERE date >= current_date GROUP BY symbol"
                )
            ).all()
        }
    return feats, earn


def cards(feats: list[dict], earn: dict[str, date]) -> list[sg.Card]:
    out = []
    for f in feats:
        ts = f.get("ts")
        d = ts.date() if isinstance(ts, datetime) else ts
        out.append(sg.evaluate(f["symbol"], f, as_of=d, next_earnings=earn.get(f["symbol"])))
    out.sort(key=lambda c: (c.flagged, c.passed, c.score), reverse=True)
    return out


def _card_html(c: sg.Card) -> str:
    color = "#4ade80" if c.lean == "bullish" else "#f87171" if c.lean == "bearish" else "#e6edf6"
    rows = []
    for g in c.gates:
        mark, cls = ("✓", "ok") if g.ok else (("✗", "no") if g.ok is False else ("–", "na"))
        rows.append(
            f'<div class="g"><span class="{cls}">{mark}</span><span>{html.escape(sg.GATE_LABELS[g.name])}</span>'
            f'<span class="v {cls}">{html.escape(g.value)}</span><span class="w">{html.escape(g.why)}</span></div>'
        )
    badge = '<span class="badge">FLAG ≥6/9</span>' if c.flagged else ""
    return (
        f'<div class="card{" flag" if c.flagged else ""}"><div class="top"><span class="sym">{html.escape(c.symbol)}</span>'
        f'<span class="lean" style="color:{color}">{c.lean}</span>{badge}<span class="cnt">{c.passed}/9</span></div>'
        f'<div class="struct">{html.escape(c.structure)} · Stage-1 {c.score:.0f} · data {c.as_of}</div>{"".join(rows)}</div>'
    )


def page(cs: list[sg.Card]) -> str:
    n_flag = sum(1 for c in cs if c.flagged)
    body = "".join(_card_html(c) for c in cs) or "<p>No banked swing_features rows.</p>"
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Swing scorecard</title><style>{_STYLE}</style></head><body>
<h1>Swing scorecard — 9 gates per ticker</h1>
<p class="sub">{len(cs)} tickers · {n_flag} flagged (≥{sg.FLAG_AT} of 9 gates pass) · generated {datetime.now():%Y-%m-%d %H:%M}</p>
<div class="grid">{body}</div>
<p class="note"><b>How to read a card.</b> The lean (bullish / bearish) comes from trend, RSI and dealer delta voting.
Each of the nine gates then asks whether that input supports a defined-risk debit spread in the lean's direction,
1–6 months out. Green ✓ = supports it, red ✗ = argues against it, grey – = no data yet (the 1-year percentile gates fill in
as daily history banks; grey never counts as a pass). A card with 6 or more greens is flagged as a candidate worth a
closer look — it is a ranking, not a trade signal. The structure line is a candidate shape, not advice.</p>
</body></html>"""


def build(
    symbols: list[str] | None = None, *, settings=None, out: Path | None = None
) -> tuple[Path, list[dict]]:
    syms = [s.upper() for s in symbols] if symbols else None
    feats, earn = fetch(syms, settings=settings)
    cs = cards(feats, earn)
    path = Path(out) if out else _OUT / f"swing_scorecard_{date.today():%Y-%m-%d}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page(cs), encoding="utf-8")
    summary = [
        {
            "symbol": c.symbol,
            "lean": c.lean,
            "passed": c.passed,
            "known": c.known,
            "flagged": c.flagged,
            "score": c.score,
            "structure": c.structure,
            "gates": {g.name: g.ok for g in c.gates},
        }
        for c in cs
    ]
    print(f"Wrote {path} ({len(cs)} cards, {sum(1 for c in cs if c.flagged)} flagged)")
    return path, summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("symbols", nargs="*")
    a = ap.parse_args()
    build(a.symbols or None)


if __name__ == "__main__":
    main()
