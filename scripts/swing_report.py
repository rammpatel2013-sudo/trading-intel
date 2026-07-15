"""On-demand swing-setup scanner (Stage-1) — live from CVForge (ADR-004).

The "give me swing setups" deliverable: for each symbol it pulls a LIVE chain,
realized-vol history, and technicals from CVForge (convexlib untouched), computes
a transparent Stage-1 conviction score, picks a defined-risk option structure,
and writes a dark-themed ``reports/swing_<date>.html`` ranked by score.

Stage-1 = ABSOLUTE thresholds (IV/RV, RSI, trend, DEX lean). Percentile features
(IV-rank, skew-percentile) fill in once the daily feature-snapshot banks enough
history (P2). Structures are CANDIDATE ideas, descriptive only — NOT signals and
not advice (FlashAlpha rule 4); the validated SignalGenerator is P4.

Run (Windows, venv):
    .venv\\Scripts\\python scripts\\swing_report.py
    .venv\\Scripts\\python scripts\\swing_report.py AAPL NVDA TSLA
"""

from __future__ import annotations

import argparse
import html
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from trading_intel.clients.cvforge import CVForgeClient
from trading_intel.config import get_settings

_REPO_ROOT = Path(__file__).resolve().parents[1]
_OUT = _REPO_ROOT / "reports"
GRN, RED, NEU, AMB = "#4ade80", "#f87171", "#cbd5e1", "#e3b341"

_STYLE = """
body { background:#0e1117; color:#e6e6e6; font-family:system-ui,Arial,sans-serif; margin:0; padding:24px; }
h1 { font-size:20px; margin:0 0 4px; } .sub { color:#8b97a7; font-size:13px; margin:0 0 18px; }
table { border-collapse:collapse; width:100%; font-size:13px; }
th,td { padding:7px 10px; text-align:right; border-bottom:1px solid #2a3550; }
th { background:#1f2a44; color:#fff; position:sticky; top:0; }
td:first-child, th:first-child { text-align:left; font-weight:600; }
tr:hover td { background:#161d2e; } .note { color:#8b97a7; font-size:12px; margin-top:16px; line-height:1.5; }
"""


def realized_vol(closes: np.ndarray, window: int = 20) -> float | None:
    """Annualized close-to-close realized vol over the last ``window`` returns."""
    if closes.size < window + 1:
        return None
    rets = np.diff(np.log(closes[-(window + 1) :]))
    return float(rets.std(ddof=1) * np.sqrt(252))


def skew_25d(chain: pd.DataFrame, *, dte_lo: int = 25, dte_hi: int = 60) -> float | None:
    """25Δ put IV − 25Δ call IV on the nearest expiry in the DTE window (put skew > 0)."""
    if chain.empty or "delta" not in chain.columns:
        return None
    df = chain.dropna(subset=["delta", "iv", "expiration"]).copy()
    dte = (df["expiration"] - pd.Timestamp(date.today())).dt.days
    df = df[(dte >= dte_lo) & (dte <= dte_hi)]
    if df.empty:
        return None
    target = df.loc[(df["expiration"] - df["expiration"].min()).abs().idxmin(), "expiration"]
    df = df[df["expiration"] == target]
    calls, puts = (
        df[df["opt_kind"].str.upper().str[0] == "C"],
        df[df["opt_kind"].str.upper().str[0] == "P"],
    )
    if calls.empty or puts.empty:
        return None
    c = calls.iloc[(calls["delta"] - 0.25).abs().argmin()]
    p = puts.iloc[(puts["delta"] + 0.25).abs().argmin()]
    return float(p["iv"] - c["iv"])


def score_setup(feat: dict) -> tuple[float, str, str]:
    """Transparent Stage-1 conviction (0-100), direction, and a defined-risk structure."""
    pts, direction = 0.0, 0
    if feat.get("px") and feat.get("sma50"):
        up = feat["px"] > feat["sma50"]
        direction += 1 if up else -1
        pts += 20 if up else 0
    rsi = feat.get("rsi")
    if rsi is not None:
        if rsi >= 55:
            direction += 1
        elif rsi <= 45:
            direction -= 1
        pts += (
            max(0.0, 20 - abs(rsi - 60) * 0.6) if rsi >= 50 else max(0.0, 20 - abs(40 - rsi) * 0.6)
        )
    if feat.get("dex") is not None:
        direction += 1 if feat["dex"] > 0 else -1
        pts += 15
    ivrv = feat.get("iv_rv")
    if ivrv is not None:
        pts += (
            25 if ivrv < 1.1 else (10 if ivrv < 1.3 else 5)
        )  # cheap vol = higher conviction to buy
    pts += 20 if feat.get("gex") is not None else 0
    score = round(min(100.0, pts), 1)

    lean = "bullish" if direction > 0 else "bearish" if direction < 0 else "neutral"
    cheap = ivrv is not None and ivrv < 1.15
    rich = ivrv is not None and ivrv > 1.3
    if lean == "bullish":
        structure = (
            "Call debit spread (45-90 DTE)"
            if not rich
            else "Bull put credit spread (harvest put skew)"
        )
    elif lean == "bearish":
        structure = "Put debit spread (45-90 DTE)" if not rich else "Bear call credit spread"
    else:
        structure = "Iron condor / stand aside" if rich else "No edge — wait"
    if cheap and lean != "neutral":
        structure += " · long premium favored (IV<RV-ish)"
    return score, lean, structure


def analyze(client: CVForgeClient, sym: str) -> dict:
    """Pull live features for one symbol. Returns a dict (with ``error`` on failure)."""
    out: dict = {"symbol": sym}
    try:
        chain = client.chain(sym)  # single /chains pull, reused for exposures + skew
        exp = client.exposures(sym, chain=chain)
        out["px"] = exp.get("spot")
        out["gex"] = exp.get("gex_total")
        out["dex"] = exp.get("dex_total")
        out["atm_iv"] = exp.get("atm_iv")
        frm = (date.today() - timedelta(days=180)).isoformat()
        bars = client.aggs(sym, frm=frm, to=date.today().isoformat())
        rv = realized_vol(bars["c"].to_numpy(dtype=float)) if not bars.empty else None
        out["rv20"] = rv
        out["iv_rv"] = (out["atm_iv"] / rv) if (rv and out.get("atm_iv")) else None
        rsi = client.fmp(
            "technical-indicators/rsi", {"symbol": sym, "periodLength": 14, "timeframe": "1day"}
        )
        out["rsi"] = float(rsi[0]["rsi"]) if isinstance(rsi, list) and rsi else None
        sma = client.fmp(
            "technical-indicators/sma", {"symbol": sym, "periodLength": 50, "timeframe": "1day"}
        )
        out["sma50"] = float(sma[0]["sma"]) if isinstance(sma, list) and sma else None
        out["skew"] = skew_25d(chain)
        out["score"], out["dir"], out["structure"] = score_setup(out)
    except Exception as exc:  # one bad symbol shouldn't kill the scan
        out["error"] = str(exc)
    return out


def _f(x: object, dp: int = 2, suf: str = "") -> str:
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "—"
    return f"{float(x):,.{dp}f}{suf}"


def render_row(r: dict) -> str:
    if r.get("error"):
        return f"<tr><td>{html.escape(r['symbol'])}</td><td colspan='11' style='text-align:left;color:{RED}'>{html.escape(r['error'][:80])}</td></tr>"
    dcol = {"bullish": GRN, "bearish": RED, "neutral": NEU}.get(r.get("dir"), NEU)
    return (
        f"<tr><td>{html.escape(r['symbol'])}</td>"
        f"<td>{_f(r.get('px'))}</td>"
        f"<td style='color:{dcol}'>{r.get('dir','—')}</td>"
        f"<td><b>{_f(r.get('score'),1)}</b></td>"
        f"<td>{_f((r.get('atm_iv') or 0)*100,1,'%')}</td>"
        f"<td>{_f((r.get('rv20') or 0)*100,1,'%')}</td>"
        f"<td>{_f(r.get('iv_rv'),2)}</td>"
        f"<td>{_f((r.get('skew') or 0)*100,1)}</td>"
        f"<td>{_f(r.get('rsi'),0)}</td>"
        f"<td>{_f(r.get('gex'),0)}</td>"
        f"<td>{_f(r.get('dex'),0)}</td>"
        f"<td style='text-align:left'>{html.escape(str(r.get('structure','—')))}</td></tr>"
    )


def render_html(rows: list[dict], *, generated: str) -> str:
    head = (
        "<tr><th>Sym</th><th>Spot</th><th>Dir</th><th>Score</th><th>ATM IV</th><th>RV20</th>"
        "<th>IV/RV</th><th>25Δ skew</th><th>RSI</th><th>GEX</th><th>DEX</th><th>Structure idea</th></tr>"
    )
    body = "".join(render_row(r) for r in rows)
    return (
        "<!doctype html><html><head><meta charset='utf-8'><title>Swing setups</title>"
        f"<style>{_STYLE}</style></head><body><h1>Swing setups &mdash; Stage-1</h1>"
        f"<p class='sub'>Live CVForge pull &middot; generated {generated} &middot; ranked by conviction</p>"
        f"<table>{head}{body}</table>"
        "<p class='note'>Stage-1 uses absolute thresholds (IV/RV, RSI, price vs SMA50, DEX lean); "
        "IV-rank &amp; skew percentiles fill in as history banks (P2). VEX/CHEX scale vs Convex is "
        "calibration-pending (ADR-004). Structure ideas are DESCRIPTIVE candidates, not signals or "
        "advice (FlashAlpha rule 4) &mdash; the validated generator + backtest are P4/P6.</p>"
        "</body></html>"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="On-demand swing-setup scanner (live CVForge).")
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
    rows.sort(key=lambda r: (r.get("score") or -1), reverse=True)

    _OUT.mkdir(exist_ok=True)
    out = Path(args.out) if args.out else _OUT / f"swing_{date.today().isoformat()}.html"
    out.write_text(
        render_html(rows, generated=pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")), encoding="utf-8"
    )
    ok = sum(1 for r in rows if not r.get("error"))
    print(f"swing report: {ok}/{len(rows)} symbols -> {out}")


if __name__ == "__main__":
    main()
