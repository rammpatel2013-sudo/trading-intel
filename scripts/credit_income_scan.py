"""Track B — market-wide credit-income scanner (weekly, on demand, live CVForge).

Ranks a BROAD universe for defined-risk *credit* structures (premium selling)
where implied vol is rich vs realized, cross-sectionally ranked so it works before
per-name IV-rank history banks. Sibling to ``scripts/swing_report.py`` (Track A =
cheap-vol debit setups); both are DESCRIPTIVE candidates, not signals/advice
(FlashAlpha rule 4). The validated writer + backtest are P4/P6.

Universe = positional symbols, else the watchlist plus a broad liquid set. A
CVForge ``/screen`` widening is best-effort (``--screen``) and degrades to the
static universe if the screener errors. Pure feature math + ranking come from
``trading_intel.swing`` so scoring lives in one place.

Run (Windows, venv):
    run_credit_income_scan.bat
    run_credit_income_scan.bat SPY QQQ AMD NVDA
Needs CVFORGE_API_KEY in .env (Go/Research tier).
"""

from __future__ import annotations

import argparse
import html
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import TypeVar

import numpy as np
import pandas as pd

from trading_intel.clients.cvforge import CVForgeClient
from trading_intel.config import get_settings
from trading_intel.errors import DataSourceError
from trading_intel.swing.credit_income import CreditIdea, rank_universe
from trading_intel.swing.features import iv_rv_ratio, realized_vol, skew_25d

_REPO_ROOT = Path(__file__).resolve().parents[1]
_OUT = _REPO_ROOT / "reports"
GRN, RED, NEU = "#4ade80", "#f87171", "#cbd5e1"

# Broad liquid names/ETFs that reliably carry sellable premium (merged with the
# watchlist). Widen live with CVForge /screen (--screen) when the schema is probed.
DEFAULT_CREDIT_UNIVERSE: tuple[str, ...] = (
    "SPY",
    "QQQ",
    "IWM",
    "DIA",
    "AAPL",
    "MSFT",
    "NVDA",
    "AMD",
    "META",
    "AMZN",
    "GOOGL",
    "TSLA",
    "NFLX",
    "AVGO",
    "SMCI",
    "COIN",
    "MSTR",
    "XOM",
    "JPM",
    "BAC",
    "XLE",
    "XLF",
    "GDX",
    "SLV",
    "GLD",
    "TLT",
    "HYG",
    "USO",
    "FXI",
    "EEM",
)

_STYLE = """
body { background:#0e1117; color:#e6e6e6; font-family:system-ui,Arial,sans-serif; margin:0; padding:24px; }
h1 { font-size:20px; margin:0 0 4px; } .sub { color:#8b97a7; font-size:13px; margin:0 0 18px; }
table { border-collapse:collapse; width:100%; font-size:13px; }
th,td { padding:7px 10px; text-align:right; border-bottom:1px solid #2a3550; }
th { background:#1f2a44; color:#fff; position:sticky; top:0; }
td:first-child, th:first-child { text-align:left; font-weight:600; }
tr:hover td { background:#161d2e; } .note { color:#8b97a7; font-size:12px; margin-top:16px; line-height:1.5; }
"""

_T = TypeVar("_T")


def _safe(fn: Callable[[], _T]) -> _T | None:
    """Run ``fn``; a transient CVForge ``DataSourceError`` (e.g. a 502) → None."""
    try:
        return fn()
    except DataSourceError:
        return None


def analyze_credit(client: CVForgeClient, sym: str) -> dict:
    """Live per-name feature dict for credit-income ranking (canonical keys)."""
    feat: dict = {"symbol": sym}
    try:
        chain = client.chain(sym)
        exp = client.exposures(sym, chain=chain)
        spot = exp.get("spot")
        atm_iv = exp.get("atm_iv")
        feat["spot"] = spot
        feat["atm_iv"] = atm_iv
        feat["gex"] = exp.get("gex_total")
        feat["dex"] = exp.get("dex_total")
        feat["skew_25d"] = skew_25d(chain)

        frm = (date.today() - timedelta(days=180)).isoformat()
        bars = _safe(lambda: client.aggs(sym, frm=frm, to=date.today().isoformat()))
        rv = (
            realized_vol(bars["c"].to_numpy(dtype=float))
            if (bars is not None and not bars.empty)
            else None
        )
        feat["rv20"] = rv
        feat["iv_rv"] = iv_rv_ratio(atm_iv, rv)
        rsi = _safe(
            lambda: client.fmp(
                "technical-indicators/rsi", {"symbol": sym, "periodLength": 14, "timeframe": "1day"}
            )
        )
        feat["rsi14"] = float(rsi[0]["rsi"]) if isinstance(rsi, list) and rsi else None
        sma = _safe(
            lambda: client.fmp(
                "technical-indicators/sma", {"symbol": sym, "periodLength": 50, "timeframe": "1day"}
            )
        )
        sma50 = float(sma[0]["sma"]) if isinstance(sma, list) and sma else None
        feat["sma50"] = sma50
        feat["px_vs_sma50"] = (spot / sma50 - 1.0) if (spot and sma50) else None
    except Exception as exc:  # core pull failed → mark row, still list it
        feat["error"] = str(exc)
    return feat


def _screen_universe(client: CVForgeClient, *, limit: int = 60) -> list[str]:
    """Best-effort CVForge breadth: liquid names by option volume; [] on any error."""
    try:
        df = client.screen(
            columns=["ticker", "day_volume"],
            filters=[{"field": "day_volume", "op": "gt", "value": 5000}],
            sort=[{"field": "day_volume", "dir": "desc"}],
            limit=limit,
        )
        if not df.empty and "ticker" in df.columns:
            return [str(t).upper() for t in df["ticker"].tolist()]
    except DataSourceError:
        pass
    return []


def _f(x: object, dp: int = 2, suf: str = "") -> str:
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "—"
    return f"{float(x):,.{dp}f}{suf}"


def render_row(i: CreditIdea, feat: dict) -> str:
    if feat.get("error"):
        return (
            f"<tr><td>{html.escape(i.symbol)}</td>"
            f"<td colspan='8' style='text-align:left;color:{RED}'>{html.escape(feat['error'][:80])}</td></tr>"
        )
    lc = {"bullish": GRN, "bearish": RED, "neutral": NEU}.get(i.lean, NEU)
    return (
        f"<tr><td>{html.escape(i.symbol)}</td>"
        f"<td><b>{_f(i.score, 1)}</b></td>"
        f"<td>{html.escape(i.side)}</td>"
        f"<td style='color:{lc}'>{html.escape(i.lean)}</td>"
        f"<td>{_f((i.atm_iv or 0) * 100, 1, '%')}</td>"
        f"<td>{_f(i.iv_rv, 2)}</td>"
        f"<td>{_f((i.iv_rv_rank or 0) * 100, 0, '%')}</td>"
        f"<td>{_f((feat.get('skew_25d') or 0) * 100, 1)}</td>"
        f"<td style='text-align:left'>{html.escape(i.structure)}</td></tr>"
    )


def render_html(ideas: list[CreditIdea], feats: dict[str, dict], *, generated: str) -> str:
    head = (
        "<tr><th>Sym</th><th>Credit score</th><th>Side</th><th>Lean</th><th>ATM IV</th>"
        "<th>IV/RV</th><th>IV/RV xs-rank</th><th>25Δ skew</th><th>Structure idea</th></tr>"
    )
    body = "".join(render_row(i, feats.get(i.symbol, {})) for i in ideas)
    return (
        "<!doctype html><html><head><meta charset='utf-8'><title>Credit-income scan</title>"
        f"<style>{_STYLE}</style></head><body><h1>Credit-income scan &mdash; Track B</h1>"
        f"<p class='sub'>Live CVForge &middot; generated {generated} &middot; ranked by vol richness "
        "(sell premium where IV is rich vs RV)</p>"
        f"<table>{head}{body}</table>"
        "<p class='note'>Richness = absolute IV/RV + cross-sectional IV/RV rank within this batch "
        "(percentiles bank forward; xs-rank is the interim). Side follows the shared lean; skew "
        "rewards the sold wing. DESCRIPTIVE candidates only, not signals or advice (FlashAlpha "
        "rule 4) &mdash; the validated generator + backtest are P4/P6.</p>"
        "</body></html>"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Market-wide credit-income scanner (live CVForge)."
    )
    parser.add_argument("symbols", nargs="*", help="tickers (default: watchlist + broad set)")
    parser.add_argument(
        "--screen", action="store_true", help="widen via CVForge /screen (best-effort)"
    )
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    settings = get_settings()
    client = CVForgeClient(settings)
    try:
        if args.symbols:
            universe = [s.upper() for s in args.symbols]
        else:
            universe = sorted(set(settings.watchlist_symbols) | set(DEFAULT_CREDIT_UNIVERSE))
            if args.screen:
                universe = sorted(set(universe) | set(_screen_universe(client)))
        feats = {sym: analyze_credit(client, sym) for sym in universe}
    finally:
        client.close()

    scored = [f for f in feats.values() if not f.get("error") and f.get("iv_rv") is not None]
    ideas = rank_universe(scored)
    # Append error/blank rows at the bottom so nothing silently disappears.
    listed = {i.symbol for i in ideas}
    from trading_intel.swing.credit_income import credit_income_score

    ideas += [credit_income_score(f) for f in feats.values() if f["symbol"] not in listed]

    _OUT.mkdir(exist_ok=True)
    out = Path(args.out) if args.out else _OUT / f"credit_income_{date.today().isoformat()}.html"
    out.write_text(
        render_html(ideas, feats, generated=pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")),
        encoding="utf-8",
    )
    ok = len(scored)
    print(f"credit-income scan: {ok}/{len(feats)} names ranked -> {out}")


if __name__ == "__main__":
    main()
