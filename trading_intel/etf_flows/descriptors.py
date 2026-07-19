"""Pure LETF issuance / forced-rebalance descriptors.

Compute over the banked ``(shares_outstanding, nav)`` series from
``letf_shares_snapshots``. Turns the raw daily snapshot into the descriptor layer
promised by ``clients.EtfFlowSource`` and migration 0032:

    dshares            day-over-day change in shares outstanding
    net issuance $     = dshares x NAV        (creation > 0, redemption < 0)
    AUM                = shares x NAV
    LETF daily return  = NAV_t / NAV_{t-1} - 1
    underlying return  = LETF return / leverage k
    forced rebalance $ = k*(k-1)*AUM*underlying_return

The forced-rebalance term is the dollar exposure a kx fund must trade in the
underlying at the close to reset constant leverage. It is procyclical for every
leveraged/inverse fund (k*(k-1) > 0 for |k| >= 1): funds buy into strength and
sell into weakness — the mechanical amplifier behind end-of-day momentum.

No I/O here (the DB read + NAV join live at the report/job edge); this module is
pure so it is unit-tested without Postgres. Regime descriptor only (FlashAlpha
rule 4): issuance/rebalance are banked and reported like GEX/DEX and NEVER emit a
signal on their own.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class SharesRow:
    """One banked reading: shares outstanding (+ optional NAV/float) on a day."""

    ts: date
    shares_outstanding: int
    nav: float | None = None
    float_shares: int | None = None


@dataclass(frozen=True, slots=True)
class FlowPoint:
    """Per-day issuance / rebalance descriptors for one LETF (all $ are dollars).

    First-in-series fields that need a prior day (``d_shares``, ``net_issuance_usd``,
    returns, ``rebalance_notional``) are ``None`` for the earliest row.
    """

    symbol: str
    ts: date
    shares_outstanding: int
    d_shares: int | None
    nav: float | None
    aum: float | None
    net_issuance_usd: float | None
    letf_return: float | None
    underlying_return: float | None
    rebalance_notional: float | None
    leverage: float | None


def compute_symbol_flows(
    symbol: str, rows: Sequence[SharesRow], *, leverage: float | None
) -> list[FlowPoint]:
    """Descriptor series for one symbol, oldest-first.

    ``rows`` need not be sorted (sorted here by ``ts``). ``leverage`` is the signed
    daily factor (e.g. +3 SOXL, -3 SOXS); pass ``None`` when unknown — dshares and
    net issuance $ still compute, but the rebalance estimate is left ``None``.
    """
    ordered = sorted(rows, key=lambda r: r.ts)
    out: list[FlowPoint] = []
    prev: SharesRow | None = None
    for r in ordered:
        aum = r.shares_outstanding * r.nav if r.nav is not None else None
        d_shares: int | None = None
        net_iss: float | None = None
        letf_ret: float | None = None
        und_ret: float | None = None
        rebal: float | None = None

        if prev is not None:
            d_shares = r.shares_outstanding - prev.shares_outstanding
            if r.nav is not None:
                net_iss = float(d_shares) * r.nav
            if prev.nav not in (None, 0) and r.nav is not None:
                letf_ret = r.nav / prev.nav - 1.0
                if leverage:  # non-zero, non-None
                    und_ret = letf_ret / leverage
                    if aum is not None:
                        rebal = leverage * (leverage - 1.0) * aum * und_ret

        out.append(
            FlowPoint(
                symbol=symbol,
                ts=r.ts,
                shares_outstanding=r.shares_outstanding,
                d_shares=d_shares,
                nav=r.nav,
                aum=aum,
                net_issuance_usd=net_iss,
                letf_return=letf_ret,
                underlying_return=und_ret,
                rebalance_notional=rebal,
                leverage=leverage,
            )
        )
        prev = r
    return out


def latest_point(points: Sequence[FlowPoint]) -> FlowPoint | None:
    """Most recent ``FlowPoint`` by ``ts`` (``None`` for an empty series)."""
    return max(points, key=lambda p: p.ts) if points else None


def bucket_totals(
    points: Iterable[FlowPoint], bucket_of: Callable[[str], str]
) -> dict[str, dict[str, float]]:
    """Sum net issuance $ and forced-rebalance $ per bucket (issuer / underlying).

    ``bucket_of`` maps a symbol to its bucket label. ``None`` descriptor values are
    skipped. Returns ``{bucket: {"net_issuance_usd": ..., "rebalance_notional": ...,
    "n": ...}}``. Aggregating a single day's ``FlowPoint`` per symbol gives that day's
    complex-level flow; aggregating a window sums the window.
    """
    out: dict[str, dict[str, float]] = {}
    for p in points:
        b = bucket_of(p.symbol)
        agg = out.setdefault(b, {"net_issuance_usd": 0.0, "rebalance_notional": 0.0, "n": 0.0})
        if p.net_issuance_usd is not None:
            agg["net_issuance_usd"] += p.net_issuance_usd
        if p.rebalance_notional is not None:
            agg["rebalance_notional"] += p.rebalance_notional
        agg["n"] += 1.0
    return out
