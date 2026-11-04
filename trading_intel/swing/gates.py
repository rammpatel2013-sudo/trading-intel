"""Swing scorecard — the 9-gate check (Phase 5 P5, 2026-11-04). Pure, no I/O.

One card per ticker. The lean (bullish / bearish) comes from the shared Stage-1
scorer (``swing.scoring.score_setup``); each gate then asks "does this input
support a defined-risk DEBIT spread in that direction, 1-6 months out?".
A gate with missing data is ``None`` (shown grey) and does NOT count as passed.
``passed >= FLAG_AT`` -> the card is flagged. Descriptor only (rule 4): the card
ranks candidates; the validated generator (``strategies/swing_options.py``) is
still the only writer to ``signals``.

The nine gates:
  1 trend      price on the lean's side of the 50-day average
  2 momentum   RSI14 in the lean's healthy band (bull 50-70, bear 30-50) — not stretched
  3 vol_cheap  ATM IV / 20-day realized < 1.15 (paying for options is fair)
  4 iv_rank    ATM IV rank (1y) below 50% (options not expensive vs their own year)
  5 skew       25-delta skew percentile not stretched against the trade
               (bull: <= 80th pct; bear: >= 20th pct)
  6 dex        dealer delta exposure leans the same way
  7 gex        dealer gamma not extreme (GEX percentile 10-90) — orderly tape
  8 earnings   no earnings report in the next 21 days (debit spreads hate the IV crush)
  9 score      Stage-1 conviction >= 60
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date

from trading_intel.swing.scoring import score_setup

FLAG_AT = 6
EARN_DAYS = 21
GATE_NAMES = (
    "trend",
    "momentum",
    "vol_cheap",
    "iv_rank",
    "skew",
    "dex",
    "gex",
    "earnings",
    "score",
)
GATE_LABELS = {
    "trend": "Trend (price vs 50-day)",
    "momentum": "Momentum (RSI14 band)",
    "vol_cheap": "Options fair vs realized (IV/RV < 1.15)",
    "iv_rank": "IV rank < 50% of its year",
    "skew": "Skew not stretched against the trade",
    "dex": "Dealer delta leans the same way",
    "gex": "Dealer gamma not extreme",
    "earnings": f"No earnings in next {EARN_DAYS} days",
    "score": "Stage-1 conviction ≥ 60",
}


@dataclass(slots=True)
class Gate:
    name: str
    ok: bool | None
    value: str
    why: str


@dataclass(slots=True)
class Card:
    symbol: str
    as_of: date | None
    lean: str
    score: float
    structure: str
    gates: list[Gate] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for g in self.gates if g.ok)

    @property
    def known(self) -> int:
        return sum(1 for g in self.gates if g.ok is not None)

    @property
    def flagged(self) -> bool:
        return self.lean != "neutral" and self.passed >= FLAG_AT


def _f(x: object) -> float | None:
    try:
        v = float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def evaluate(
    symbol: str,
    feat: Mapping[str, object],
    *,
    as_of: date | None = None,
    next_earnings: date | None = None,
) -> Card:
    """Score one ``swing_features`` row (canonical keys) into a 9-gate card."""
    sc = score_setup(feat)
    lean = sc.lean
    bull = lean == "bullish"
    card = Card(symbol, as_of, lean, sc.score, sc.structure)
    G = card.gates.append

    pv = _f(feat.get("px_vs_sma50"))
    if pv is None or lean == "neutral":
        G(Gate("trend", None, "—" if pv is None else f"{pv * 100:+.1f}%", "no lean / no SMA50"))
    else:
        G(
            Gate(
                "trend",
                (pv > 0) if bull else (pv < 0),
                f"{pv * 100:+.1f}% vs SMA50",
                (
                    "price on the lean's side of the 50-day"
                    if ((pv > 0) == bull)
                    else "price on the wrong side of the 50-day"
                ),
            )
        )

    rsi = _f(feat.get("rsi14"))
    if rsi is None or lean == "neutral":
        G(Gate("momentum", None, "—" if rsi is None else f"{rsi:.0f}", "no lean / no RSI"))
    else:
        ok = 50 <= rsi <= 70 if bull else 30 <= rsi <= 50
        G(
            Gate(
                "momentum",
                ok,
                f"RSI {rsi:.0f}",
                "healthy band" if ok else "outside the band (stretched or wrong way)",
            )
        )

    ivrv = _f(feat.get("iv_rv"))
    G(
        Gate(
            "vol_cheap",
            None if ivrv is None else ivrv < 1.15,
            "—" if ivrv is None else f"{ivrv:.2f}×",
            (
                "IV not much above realized"
                if ivrv is not None and ivrv < 1.15
                else "IV rich vs realized" if ivrv is not None else "no IV/RV"
            ),
        )
    )

    rk = _f(feat.get("atm_iv_rank_252d"))
    G(
        Gate(
            "iv_rank",
            None if rk is None else rk < 0.5,
            "—" if rk is None else f"{rk * 100:.0f}%",
            (
                "not enough banked history yet"
                if rk is None
                else ("cheap vs its year" if rk < 0.5 else "expensive vs its year")
            ),
        )
    )

    sk = _f(feat.get("skew_pctile_252d"))
    if sk is None or lean == "neutral":
        G(
            Gate(
                "skew",
                None,
                "—" if sk is None else f"{sk * 100:.0f}%",
                "no lean / not enough banked history",
            )
        )
    else:
        ok = sk <= 0.8 if bull else sk >= 0.2
        G(
            Gate(
                "skew",
                ok,
                f"{sk * 100:.0f}th pct",
                "skew fine for the trade" if ok else "skew stretched against the trade",
            )
        )

    dex = _f(feat.get("dex"))
    if dex is None or lean == "neutral":
        G(Gate("dex", None, "—", "no lean / no DEX"))
    else:
        ok = (dex > 0) if bull else (dex < 0)
        G(
            Gate(
                "dex",
                ok,
                "long" if dex > 0 else "short",
                "dealer delta agrees" if ok else "dealer delta disagrees",
            )
        )

    gp = _f(feat.get("gex_pctile_252d"))
    G(
        Gate(
            "gex",
            None if gp is None else 0.10 <= gp <= 0.90,
            "—" if gp is None else f"{gp * 100:.0f}th pct",
            (
                "not enough banked history"
                if gp is None
                else ("orderly" if 0.10 <= gp <= 0.90 else "extreme gamma — jumpy or pinned")
            ),
        )
    )

    if as_of is None:
        G(Gate("earnings", None, "—", "no date"))
    elif next_earnings is None:
        G(Gate("earnings", True, "none found", f"no report in the next {EARN_DAYS} days"))
    else:
        days = (next_earnings - as_of).days
        ok = days > EARN_DAYS
        G(
            Gate(
                "earnings",
                ok,
                f"{next_earnings.isoformat()} ({days}d)",
                "clear of the report" if ok else "report inside the window — IV crush risk",
            )
        )

    G(
        Gate(
            "score",
            sc.score >= 60,
            f"{sc.score:.0f}",
            "conviction high enough" if sc.score >= 60 else "conviction low",
        )
    )
    return card


def price_only_gates(close: list[float], i: int) -> tuple[str, bool, bool] | None:
    """Gates 1-2 rebuilt from closes alone (for the P6 backtest).

    Returns (lean, trend_ok, momentum_ok) at index ``i`` using SMA50 + RSI14 only —
    the IV/skew/dealer gates have no history before the features banked.
    """
    if i < 50:
        return None
    sma = sum(close[i - 49 : i + 1]) / 50
    gains = losses = 0.0
    for j in range(i - 13, i + 1):
        ch = close[j] - close[j - 1]
        gains += max(ch, 0.0)
        losses += max(-ch, 0.0)
    rsi = 100.0 if losses == 0 else 100.0 - 100.0 / (1.0 + gains / losses)
    up = close[i] > sma
    vote = (1 if up else -1) + (1 if rsi >= 55 else -1 if rsi <= 45 else 0)
    lean = "bullish" if vote > 0 else "bearish" if vote < 0 else "neutral"
    if lean == "neutral":
        return lean, False, False
    bull = lean == "bullish"
    return lean, (up if bull else not up), (50 <= rsi <= 70 if bull else 30 <= rsi <= 50)
