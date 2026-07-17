"""Flow-intelligence read-side aggregations (the Power-BI-style view, from our tape).

Pure pandas over a ``tas_prints``-shaped frame (columns ``cp`` C/P, ``side``
buy/sell, ``notional`` = premium $, ``size``, ``expiry``). Turns the raw tape into
the panels that board shows — all from data we already bank, no new vendor:

    * net premium 4-way   call-buy / put-sell / put-buy / call-sell (+ a signed
                          bullish-minus-bearish premium tilt)
    * premium by size     retail / medium / large-institutional (by print premium)
    * premium by DTE      <7 / 7-31 / 31-93 / >93 day buckets

Only ``buy``/``sell`` prints feed the directional 4-way (``mid``/``unknown`` can't
be classified, so they're excluded rather than guessed). Descriptive flow only
(FlashAlpha rule 4). No I/O — the DB read + rendering live at the report/page edge.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import pandas as pd

# Default per-print premium ($) cutoffs for the size buckets (tunable).
RETAIL_MAX = 25_000.0
LARGE_MIN = 250_000.0
# Default DTE bucket edges (days): <7, 7-31, 31-93, >93.
DTE_EDGES = (7, 31, 93)


@dataclass(frozen=True, slots=True)
class FourWay:
    """Net option premium split by call/put x buy/sell (all $ magnitudes >= 0)."""

    call_buy: float
    call_sell: float
    put_buy: float
    put_sell: float

    @property
    def bullish_premium(self) -> float:
        """Call-buy + put-sell (long-delta / short-downside premium)."""
        return self.call_buy + self.put_sell

    @property
    def bearish_premium(self) -> float:
        """Put-buy + call-sell (short-delta / short-upside premium)."""
        return self.put_buy + self.call_sell

    @property
    def net_premium(self) -> float:
        """Bullish - bearish premium (signed tilt)."""
        return self.bullish_premium - self.bearish_premium


def _norm(prints: pd.DataFrame) -> pd.DataFrame:
    """Normalized working copy: upper cp initial, lower side, numeric notional."""
    if prints is None or prints.empty:
        return pd.DataFrame(columns=["_cp", "_side", "_notional", "size", "expiry"])
    df = prints.copy()
    df["_cp"] = df["cp"].astype(str).str.upper().str[0]
    df["_side"] = df["side"].astype(str).str.lower()
    df["_notional"] = pd.to_numeric(df["notional"], errors="coerce").fillna(0.0)
    return df


def net_premium_4way(prints: pd.DataFrame) -> FourWay:
    """Premium ($) summed by call/put x buy/sell over the (buy/sell) prints."""
    df = _norm(prints)

    def s(cp: str, side: str) -> float:
        return float(df.loc[(df["_cp"] == cp) & (df["_side"] == side), "_notional"].sum())

    return FourWay(
        call_buy=s("C", "buy"),
        call_sell=s("C", "sell"),
        put_buy=s("P", "buy"),
        put_sell=s("P", "sell"),
    )


def premium_by_size(
    prints: pd.DataFrame, *, retail_max: float = RETAIL_MAX, large_min: float = LARGE_MIN
) -> dict[str, float]:
    """Total premium ($) per size bucket by per-print premium (retail/medium/large)."""
    df = _norm(prints)
    out = {"retail": 0.0, "medium": 0.0, "large": 0.0}
    if df.empty:
        return out
    n = df["_notional"]
    out["retail"] = float(n[n < retail_max].sum())
    out["large"] = float(n[n >= large_min].sum())
    out["medium"] = float(n[(n >= retail_max) & (n < large_min)].sum())
    return out


def premium_by_dte(
    prints: pd.DataFrame, *, ref: date | None = None, edges: tuple[int, int, int] = DTE_EDGES
) -> dict[str, float]:
    """Total premium ($) per DTE bucket (<e0 / e0-e1 / e1-e2 / >e2) from ``expiry``."""
    lo, mid, hi = edges
    labels = (f"<{lo}", f"{lo}-{mid}", f"{mid}-{hi}", f">{hi}")
    out = dict.fromkeys(labels, 0.0)
    df = _norm(prints)
    if df.empty or "expiry" not in df.columns:
        return out
    ref = ref or date.today()
    dte = (pd.to_datetime(df["expiry"], errors="coerce") - pd.Timestamp(ref)).dt.days
    n = df["_notional"]
    out[labels[0]] = float(n[dte < lo].sum())
    out[labels[1]] = float(n[(dte >= lo) & (dte < mid)].sum())
    out[labels[2]] = float(n[(dte >= mid) & (dte < hi)].sum())
    out[labels[3]] = float(n[dte >= hi].sum())
    return out


def build_flow_payload(
    symbol: str,
    trade_date: date | None,
    prints: pd.DataFrame,
    *,
    daily: dict | None = None,
    contracts: Sequence[dict] = (),
    top: int = 8,
) -> dict:
    """Assemble the flow-intelligence MCP payload (pure).

    ``daily`` / ``contracts`` are plain dicts projected from ``tas_daily_flow`` /
    ``tas_daily_contract`` at the DB edge; the 4-way / size / DTE come from the raw
    ``prints`` frame. Descriptive only (FlashAlpha rule 4).
    """
    fw = net_premium_4way(prints)
    return {
        "symbol": symbol.upper(),
        "trade_date": trade_date.isoformat() if trade_date else None,
        "n_prints": len(prints) if prints is not None else 0,
        "net_premium_4way": {
            "call_buy": fw.call_buy,
            "put_sell": fw.put_sell,
            "put_buy": fw.put_buy,
            "call_sell": fw.call_sell,
            "bullish_premium": fw.bullish_premium,
            "bearish_premium": fw.bearish_premium,
            "net_premium": fw.net_premium,
        },
        "premium_by_size": premium_by_size(prints),
        "premium_by_dte": premium_by_dte(prints, ref=trade_date),
        "accumulation": daily,
        "top_contracts": [dict(c) for c in list(contracts)[:top]],
        "note": (
            "Descriptive tape flow, not a signal (FlashAlpha rule 4). "
            "buy/sell = tape aggressor side, not an OI-change guess."
        ),
    }
