"""Sentiment inputs (institutional 13F + analyst ratings/targets) — pure, tolerant.

Value object + the handful of pure derivations we bank alongside the raw fields
(implied upside to the average target, Buy-share of the rating panel). No I/O — the
FMP pull + persistence live in ``scheduler/jobs/sentiment.py``; unit-tested here.

Descriptive research descriptors only (FlashAlpha rule 4) — never a standalone signal.
"""

from __future__ import annotations

from dataclasses import dataclass

# Raw fields banked one row per (symbol, ts). rating_consensus (str) is handled
# separately by the job; every numeric field is stored as Float (repo convention,
# matching ``fundamentals_snapshots``).
RAW_FIELDS: tuple[str, ...] = (
    "inst_pct",
    "inst_holders",
    "inst_shares",
    "inst_net_share_change",
    "inst_new_positions",
    "inst_closed_positions",
    "inst_put_call",
    "pt_avg",
    "pt_high",
    "pt_low",
    "rating_buy",
    "rating_hold",
    "rating_sell",
    "num_analysts",
    "price",
)
DERIVED_FIELDS: tuple[str, ...] = ("pt_upside_pct", "buy_share")


@dataclass(frozen=True, slots=True)
class SentimentInputs:
    """Raw per-name sentiment inputs (all optional; a missing field is ``None``)."""

    symbol: str
    inst_pct: float | None = None
    inst_holders: float | None = None
    inst_shares: float | None = None
    inst_net_share_change: float | None = None
    inst_new_positions: float | None = None
    inst_closed_positions: float | None = None
    inst_put_call: float | None = None
    pt_avg: float | None = None
    pt_high: float | None = None
    pt_low: float | None = None
    rating_buy: float | None = None
    rating_hold: float | None = None
    rating_sell: float | None = None
    num_analysts: float | None = None
    price: float | None = None
    rating_consensus: str | None = None


def derived_fields(inp: SentimentInputs) -> dict[str, float | None]:
    """Pure derivations banked beside the raw fields.

    - ``pt_upside_pct``: average price target vs last price, as a fraction.
    - ``buy_share``: Buy / (Buy + Hold + Sell) of the analyst rating panel.
    """
    upside: float | None = None
    if inp.pt_avg is not None and inp.price not in (None, 0):
        upside = inp.pt_avg / inp.price - 1.0

    buy_share: float | None = None
    counts = [c for c in (inp.rating_buy, inp.rating_hold, inp.rating_sell) if c is not None]
    total = sum(counts)
    if inp.rating_buy is not None and total > 0:
        buy_share = inp.rating_buy / total

    return {"pt_upside_pct": upside, "buy_share": buy_share}
