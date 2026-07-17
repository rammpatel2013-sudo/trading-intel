"""On-demand earnings-call inflection scan (Slice 1, live CVForge — free transcripts).

For each ticker: pull the two most recent earnings-call transcripts, measure the
quarter-over-quarter tone change + guidance cues, and rank names by a positive /
negative *inflection* read. Descriptive candidates only, not signals or advice
(FlashAlpha rule 4). The Ollama quote-extraction (the specific inflection lines)
is Slice 2.

Run (Windows, venv):
    run_earnings_inflection.bat
    run_earnings_inflection.bat AAPL NVDA TSLA
Needs CVFORGE_API_KEY in .env.
"""

from __future__ import annotations

import argparse
import html
from datetime import date
from pathlib import Path

import pandas as pd

from trading_intel.clients.cvforge import CVForgeClient
from trading_intel.config import get_settings
from trading_intel.earnings import InflectionRead, read_inflection
from trading_intel.earnings import transcripts as tx
from trading_intel.errors import DataSourceError

_REPO_ROOT = Path(__file__).resolve().parents[1]
_OUT = _REPO_ROOT / "reports"
GRN, RED, NEU = "#4ade80", "#f87171", "#cbd5e1"

_STYLE = """
body { background:#0e1117; color:#e6e6e6; font-family:system-ui,Arial,sans-serif; margin:0; padding:24px; }
h1 { font-size:20px; margin:0 0 4px; } .sub { color:#8b97a7; font-size:13px; margin:0 0 18px; }
table { border-collapse:collapse; width:100%; font-size:13px; }
th,td { padding:7px 10px; text-align:right; border-bottom:1px solid #2a3550; }
th { background:#1f2a44; color:#fff; position:sticky; top:0; }
td:first-child, th:first-child { text-align:left; font-weight:600; }
tr:hover td { background:#161d2e; } .note { color:#8b97a7; font-size:12px; margin-top:16px; line-height:1.5; }
"""


def analyze(client: CVForgeClient, sym: str) -> dict:
    """Latest-two transcripts -> QoQ inflection read for one name."""
    out: dict = {"symbol": sym}
    try:
        recs = tx.latest_two(client, sym)
        if not recs:
            out["error"] = "no transcript available"
            return out
        this = recs[0]
        prior = recs[1] if len(recs) > 1 else None
        out["period"] = f"{this.get('period', '')} {this.get('year', '')}".strip() or this.get(
            "date"
        )
        out["read"] = read_inflection(
            sym, this.get("content", ""), prior.get("content") if prior else None
        )
    except DataSourceError as exc:
        out["error"] = str(exc)[:80]
    return out


def _f(x: object, dp: int = 2) -> str:
    if x is None:
        return "—"
    return f"{float(x):,.{dp}f}"


def render_row(r: dict) -> str:
    if r.get("error"):
        return (
            f"<tr><td>{html.escape(r['symbol'])}</td>"
            f"<td colspan='7' style='text-align:left;color:{RED}'>{html.escape(r['error'])}</td></tr>"
        )
    read: InflectionRead = r["read"]
    color = {"positive inflection": GRN, "negative inflection": RED}.get(read.label, NEU)
    return (
        f"<tr><td>{html.escape(r['symbol'])}</td>"
        f"<td>{html.escape(str(r.get('period', '—')))}</td>"
        f"<td style='color:{color}'><b>{_f(read.score)}</b></td>"
        f"<td style='color:{color}'>{html.escape(read.label)}</td>"
        f"<td>{_f(read.tone)}</td>"
        f"<td>{_f(read.tone_delta)}</td>"
        f"<td>{_f(read.guidance_signal)}</td>"
        f"<td>{_f(read.uncertainty_density, 4)}</td></tr>"
    )


def render_html(rows: list[dict], *, generated: str) -> str:
    head = (
        "<tr><th>Sym</th><th>Latest call</th><th>Inflection</th><th>Read</th>"
        "<th>Tone</th><th>QoQ Δtone</th><th>Guidance</th><th>Uncertainty</th></tr>"
    )
    body = "".join(render_row(r) for r in rows)
    return (
        "<!doctype html><html><head><meta charset='utf-8'><title>Earnings inflection</title>"
        f"<style>{_STYLE}</style></head><body><h1>Earnings-call inflection &mdash; Stage-1</h1>"
        f"<p class='sub'>Live CVForge transcripts &middot; generated {generated} &middot; "
        "ranked by QoQ tone change + guidance cues</p>"
        f"<table>{head}{body}</table>"
        "<p class='note'>Inflection = the quarter-over-quarter <b>change</b> in lexicon tone "
        "(0.6) + guidance raise/cut cues (0.4), tempered by rising uncertainty. Transparent "
        "Stage-1 cue lexicon; the Ollama quote-extraction (the exact inflection lines) is Slice 2. "
        "DESCRIPTIVE candidates only, not signals or advice (FlashAlpha rule 4).</p>"
        "</body></html>"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Earnings-call inflection scan (live CVForge).")
    parser.add_argument("symbols", nargs="*", help="tickers (default: WATCHLIST from .env)")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    settings = get_settings()
    symbols = [s.upper() for s in args.symbols] or settings.watchlist_symbols
    client = CVForgeClient(settings)
    try:
        rows = [analyze(client, s) for s in symbols]
    finally:
        client.close()
    rows.sort(key=lambda r: (r["read"].score if r.get("read") else 0.0), reverse=True)

    _OUT.mkdir(exist_ok=True)
    out = (
        Path(args.out)
        if args.out
        else _OUT / f"earnings_inflection_{date.today().isoformat()}.html"
    )
    out.write_text(
        render_html(rows, generated=pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")), encoding="utf-8"
    )
    ok = sum(1 for r in rows if r.get("read"))
    print(f"earnings inflection: {ok}/{len(rows)} names -> {out}")


if __name__ == "__main__":
    main()
