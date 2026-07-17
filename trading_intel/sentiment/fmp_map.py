"""Map CVForge FMP institutional + analyst payloads -> ``SentimentInputs`` (pure, tolerant).

FMP spells fields several ways and returns a list-of-one or a dict, so each metric lists
candidate keys and the first numeric/string hit wins; a missing key becomes ``None``
(graceful). Pure, so the mapping is unit-tested without a vendor.

NOTE: confirm the FMP /stable endpoint names + field spellings against the live payloads
on first run — ``institutional-ownership/symbol-ownership`` (quarters, newest first),
``price-target-consensus``, ``grades-consensus``, ``quote``. The candidate lists below are
best-effort and degrade gracefully if a key is absent.
"""

from __future__ import annotations

from collections.abc import Mapping

from trading_intel.sentiment.compute import SentimentInputs

_INST_KEYS: dict[str, tuple[str, ...]] = {
    "inst_pct": ("ownershipPercent", "institutionalOwnershipPercentage"),
    "inst_holders": ("investorsHolding", "numberOfInstitutionalHolders"),
    "inst_shares": ("numberOf13Fshares", "numberOf13FShares"),
    "inst_net_share_change": ("numberOf13FsharesChange", "numberOf13FSharesChange"),
    "inst_new_positions": ("newPositions",),
    "inst_closed_positions": ("closedPositions",),
    "inst_put_call": ("putCallRatio",),
}
_TARGET_KEYS: dict[str, tuple[str, ...]] = {
    "pt_avg": ("targetConsensus", "targetMean", "targetMedian"),
    "pt_high": ("targetHigh",),
    "pt_low": ("targetLow",),
}
_GRADE_KEYS: dict[str, tuple[str, ...]] = {
    "strong_buy": ("strongBuy",),
    "buy": ("buy",),
    "hold": ("hold",),
    "sell": ("sell",),
    "strong_sell": ("strongSell",),
}


def _num(d: Mapping[str, object], keys: tuple[str, ...]) -> float | None:
    for k in keys:
        v = d.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
        if isinstance(v, str):
            try:
                return float(v)
            except ValueError:
                continue
    return None


def _rec(payload: object) -> Mapping[str, object]:
    """FMP returns a list-of-one or a dict; normalize to the record dict."""
    if isinstance(payload, list):
        return payload[0] if payload and isinstance(payload[0], Mapping) else {}
    return payload if isinstance(payload, Mapping) else {}


def extract_inputs(
    symbol: str,
    *,
    inst: object = None,
    targets: object = None,
    grades: object = None,
    quote: object = None,
) -> SentimentInputs:
    """Build ``SentimentInputs`` from the fetched FMP payloads (latest 13F quarter)."""
    inst_r = _rec(inst)  # institutional endpoint is newest-first -> first record
    tgt_r, grd_r, q_r = _rec(targets), _rec(grades), _rec(quote)

    kw: dict[str, float | None] = {}
    for metric, keys in _INST_KEYS.items():
        kw[metric] = _num(inst_r, keys)
    for metric, keys in _TARGET_KEYS.items():
        kw[metric] = _num(tgt_r, keys)

    sb = _num(grd_r, _GRADE_KEYS["strong_buy"]) or 0.0
    b = _num(grd_r, _GRADE_KEYS["buy"]) or 0.0
    h = _num(grd_r, _GRADE_KEYS["hold"]) or 0.0
    s = _num(grd_r, _GRADE_KEYS["sell"]) or 0.0
    ss = _num(grd_r, _GRADE_KEYS["strong_sell"]) or 0.0
    rating_buy, rating_sell, total = sb + b, s + ss, sb + b + h + s + ss
    if total:
        kw["rating_buy"] = rating_buy
        kw["rating_hold"] = h
        kw["rating_sell"] = rating_sell
        kw["num_analysts"] = total

    kw["price"] = _num(q_r, ("price", "previousClose"))

    raw_consensus = grd_r.get("consensus") if isinstance(grd_r, Mapping) else None
    consensus = str(raw_consensus) if isinstance(raw_consensus, str) else None

    return SentimentInputs(
        symbol=symbol,
        rating_consensus=consensus,
        **{k: v for k, v in kw.items() if v is not None},
    )
