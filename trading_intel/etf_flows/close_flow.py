"""Leveraged-ETF close flow - the forced end-of-day rebalance, estimated live (2026-11-19).

Every leveraged / inverse fund must reset its exposure near the close. For one fund with signed daily
leverage ``L`` and assets ``AUM`` (shares x prior close), when the index has moved ``r`` since the prior
close the fund must trade::

    rebalance $ = L * (L - 1) * AUM * r  =  (L - 1) * AUM * fund_return

``L*(L-1)`` is positive for bull AND bear funds (3x -> 6, 2x -> 2, -1x -> 2, -2x -> 6, -3x -> 12), so on
a down day every fund sells and on an up day every fund buys. Summed per index and divided by one
future (ES = 50 x SPX, NQ = 20 x NDX) it is the "close flow" card. The sign flips where today's index
move is zero = the index's prior close.

We use each fund's OWN move (its 1-minute bars) when its last bar is fresh, else ``L x`` the index move.
Pure functions only; ``scheduler/jobs/letf_close_live.py`` pulls the data into the DB and calls these.
Descriptor only (rule 4). Not tested against realised close moves ([stated] 2026-11-19 "no testing").
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from trading_intel.etf_flows.registry import REGISTRY, LetfMeta

# index -> FMP symbol, future, $ per index point of one future
GROUPS: dict[str, dict] = {
    "SPX": {"fmp": "^GSPC", "future": "ES", "mult": 50.0, "name": "S&P 500"},
    "NDX": {"fmp": "^NDX", "future": "NQ", "mult": 20.0, "name": "Nasdaq-100"},
}
# |$| thresholds -> label (ES ~$360k: 2,000 ES ~ $0.7B; the 11/19 card showed 4,500 ES = MODERATE)
STRENGTH: tuple[tuple[float, str], ...] = ((0.7e9, "LIGHT"), (2.2e9, "MODERATE"), (4.4e9, "HEAVY"))
FLAT_USD = 50e6
FRESH = timedelta(minutes=20)  # a fund bar older than this -> use L x index move
# same as scripts/cvxmap/market_cal.py EARLY_CLOSE (a test keeps them equal)
EARLY_CLOSE: dict[date, tuple[int, int]] = {
    date(2026, 11, 27): (13, 0),
    date(2026, 12, 24): (13, 0),
    date(2027, 11, 26): (13, 0),
}


def session_close(d: date) -> time:
    h, m = EARLY_CLOSE.get(d, (16, 0))
    return time(h, m)


def funds_for(group: str) -> list[LetfMeta]:
    return [m for m in REGISTRY.values() if m.underlying == group]


def strength(usd: float) -> str:
    a = abs(usd)
    for lim, lab in STRENGTH:
        if a < lim:
            return lab
    return "EXTREME"


@dataclass(frozen=True)
class FundIn:
    symbol: str
    leverage: float
    shares: float | None
    prev_close: float | None
    last: float | None
    last_ts: datetime | None


def fund_flow(f: FundIn, index_ret: float | None, now: datetime) -> dict:
    """One fund's forced rebalance $ (None when shares / prior close are missing)."""
    out = {"symbol": f.symbol, "leverage": f.leverage, "aum_usd": None, "fund_ret": None,
           "used": None, "rebalance_usd": None, "per_1pct_usd": None}
    if not f.shares or not f.prev_close:
        out["used"] = "missing"
        return out
    aum = float(f.shares) * float(f.prev_close)
    out["aum_usd"] = aum
    out["per_1pct_usd"] = f.leverage * (f.leverage - 1.0) * aum * 0.01
    fresh = f.last and f.last_ts is not None and now - f.last_ts <= FRESH
    if fresh:
        ret = float(f.last) / float(f.prev_close) - 1.0
        out["used"] = "own"
    elif index_ret is not None:
        ret = f.leverage * index_ret
        out["used"] = "index"
    else:
        out["used"] = "missing"
        return out
    out["fund_ret"] = ret
    out["rebalance_usd"] = (f.leverage - 1.0) * aum * ret
    return out


def group_flow(group: str, funds: Sequence[FundIn], index_prev: float | None,
               index_last: float | None, now: datetime) -> dict:
    """Total close flow for one index: $, futures, direction, strength, flip level, per-fund rows."""
    g = GROUPS[group]
    ret = (index_last / index_prev - 1.0) if index_prev and index_last else None
    rows = [fund_flow(f, ret, now) for f in funds]
    total = sum(r["rebalance_usd"] or 0.0 for r in rows)
    per1 = sum(r["per_1pct_usd"] or 0.0 for r in rows)
    level = index_last or index_prev
    fut = total / (g["mult"] * level) if level else None
    direction = "FLAT" if abs(total) < FLAT_USD else ("BUY" if total > 0 else "SELL")
    return {
        "group": group,
        "name": g["name"],
        "future": g["future"],
        "index_prev": index_prev,
        "index_last": index_last,
        "index_ret": ret,
        "rebalance_usd": total,
        "futures": fut,
        "direction": direction,
        "strength": strength(total),
        "flip_level": index_prev,  # the sign changes where today's move is zero
        "per_1pct_usd": per1,
        "aum_usd": sum(r["aum_usd"] or 0.0 for r in rows),
        "n_funds": len(rows),
        "n_index_fallback": sum(1 for r in rows if r["used"] == "index"),
        "n_missing": sum(1 for r in rows if r["used"] == "missing"),
        "funds": sorted(rows, key=lambda r: -abs(r["rebalance_usd"] or 0.0)),
    }


def prev_session(d: date, is_session) -> date:  # noqa: ANN001 - is_session(date) -> bool
    p = d - timedelta(days=1)
    while not is_session(p):
        p -= timedelta(days=1)
    return p


def fmt_usd(x: float | None) -> str:
    if x is None:
        return "-"
    a = abs(x)
    s = f"${a / 1e9:.2f}B" if a >= 1e9 else f"${a / 1e6:.0f}M"
    return ("-" if x < 0 else "") + s


def telegram_line(groups: Iterable[dict], at: datetime) -> str:
    parts = []
    for g in groups:
        if g["index_last"] is None:
            continue
        arrow = {"SELL": "▼", "BUY": "▲"}.get(g["direction"], "•")
        parts.append(
            f"{arrow} {g['group']} {g['direction']} ≈ {fmt_usd(abs(g['rebalance_usd']))} ≈ "
            f"{abs(g['futures'] or 0):,.0f} {g['future']} ({g['strength']}, flips "
            f"{'above' if g['direction'] == 'SELL' else 'below'} {g['flip_level']:,.2f})"
        )
    return f"<b>LETF close flow {at:%H:%M} ET</b> (forced rebalance into the close)\n" + "\n".join(parts)
