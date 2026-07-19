"""External data clients.

The `OptionsDataSource` Protocol is the contract every options-data vendor
must satisfy. Code in `greeks/`, `strategies/`, `synthesis/`, and `dashboard/`
depends on this Protocol — NOT on `ConvexClient` directly. This keeps vendor
swap-out cheap.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

import pandas as pd


class OptionsDataSource(Protocol):
    """Contract for any options data vendor (Convex, Schwab, Barchart, Tradier)."""

    def chain(
        self,
        symbol: str,
        *,
        exps: tuple[int, ...] = (1, 2, 3),
        strike_range: float = 0.15,
    ) -> pd.DataFrame:
        """Return a normalized options chain.

        Required columns:
            strike, expiration, opt_kind (C/P), delta, gamma, theta, vega,
            vanna, charm, iv, oi, oi_change, volume, gxoi, dxoi, vxoi, cxoi

        ``oi_change`` is the vendor's day-over-day open-interest change (Convex
        ``oi_ch``); it may be NaN when the vendor omits it.
        """
        ...

    def underlying(
        self,
        symbols: list[str],
        *,
        time_buckets: tuple[str, ...] = ("5m", "15m", "30m"),
    ) -> pd.DataFrame:
        """Return per-symbol underlying-level data including flow metrics."""
        ...

    def spot(self, symbol: str) -> float:
        """Return the current underlying price for ``symbol``.

        A cheap price-only lookup (no chain pull) for callers that need spot to
        anchor an exposure computation, e.g. the intraday 0DTE flow collector.
        """
        ...


    def exposures(self, symbol: str, exps: tuple[int, ...] = (1, 2, 3)) -> dict:
        """Aggregate GEX/DEX/VEX/CHEX + flip point for one symbol."""
        ...

    def flow_chain(
        self,
        symbol: str,
        *,
        exps: tuple[int, ...] = (1, 2, 3, 4, 5, 6, 7, 8),
        strike_range: float = 0.15,
    ) -> pd.DataFrame:
        """Per-strike flow chain: premium ($ notional), volume, IV, OI.

        Normalized to ``opt_kind``/``premium``/``volume``/``iv`` so it feeds
        ``strategies.options_flow.aggregate_flow`` directly.
        """
        ...

    def time_and_sales(
        self, symbol: str | None = None, *, limit: int = 200, day: int = 0,
        orderby: str = "value",
    ) -> pd.DataFrame:
        """Per-trade time & sales (single sweeps / blocks).

        Market-wide by default (``symbol=None`` -> every name's prints; the
        ``symbol`` column identifies each contract). Pass a ``symbol`` to filter
        to one root. ``day`` selects the session: 0 = today, 1 = prior session,
        etc. (after the 4pm close today's tape returns zeroed trade fields, and
        prior sessions are not served - the feed is live-only during RTH).
        ``orderby="value"`` surfaces the biggest-premium prints; ``orderby="time"``
        is the chronological tape (what a polling flow collector wants).
        """
        ...

    def vix_chain(
        self,
        *,
        exps: tuple[int, ...] = (1, 2, 3),
        strike_range: float = 0.50,
    ) -> pd.DataFrame:
        """Return the VIX options chain.

        Same column contract as ``chain()`` but for VIX options. VIX options
        carry a *call* skew (the structural OTM-call bid from tail-risk
        hedgers) - the opposite of equity put skew. ``strike_range`` is wider
        because OTM VIX-call hedges trade far from spot.

        The Convex symbol convention for the VIX is vendor-dependent (one of
        ``VIX`` / ``^VIX`` / ``_VIX`` / ``$VIX``) - implementations must pick
        the working form and document it. Per ADR-003 section 7 open question.
        """
        ...

    def health(self) -> dict:
        """Vendor connectivity, rate-limit status, last call latency."""
        ...


class PriceDataSource(Protocol):
    """Contract for any daily price-history vendor (yfinance, Schwab, ...).

    Kept separate from ``OptionsDataSource``: price history is a different
    concern (and a different vendor) from the options chain. yfinance covers
    daily bars today; a Schwab implementation can satisfy the same Protocol
    later to add more granular history.
    """

    def daily_history(self, symbol: str, *, period: str = "5y") -> pd.DataFrame:
        """Return a daily OHLCV frame for ``symbol``, oldest first.

        Required columns: ``date`` (naive datetime, normalized to midnight),
        ``open``, ``high``, ``low``, ``close``, ``volume``. ``period`` is a
        vendor-style window string (e.g. ``"5y"``, ``"max"``, ``"6mo"``).
        Empty frame when the vendor has no data for the symbol.
        """
        ...


@dataclass(frozen=True, slots=True)
class SharesSnapshot:
    """One vendor reading of an ETF/LETF's shares outstanding.

    A value object at the ``clients/`` boundary (CLAUDE.md — dataclass for value
    objects). ``as_of`` is the vendor's stated date for the figure (may lag the
    caller's snapshot day, or be ``None`` when the vendor omits it). Counts are
    whole shares; ``float_shares`` is the free float when the vendor provides it.
    """

    symbol: str
    shares_outstanding: int
    as_of: date | None = None
    float_shares: int | None = None
    source: str | None = None


class EtfFlowSource(Protocol):
    """Contract for a vendor that reports ETF/LETF shares outstanding.

    The input for LETF net creation/redemption (issuance) flow: net issuance $ =
    Δ(shares_outstanding) × price, and a k× fund's forced EOD rebalance in the
    underlying ≈ k(k-1) × assets × return. FMP's stable tier serves only the
    CURRENT figure (no history), so callers snapshot this daily and bank the
    Δshares series forward (cf. the percentile bank-forward pattern) — see
    ``scheduler/jobs/letf_flows.py``.

    Descriptive research input only (FlashAlpha rule 4): issuance is a regime
    descriptor like GEX/DEX, never on its own an alert source.
    """

    def shares_outstanding(self, symbol: str) -> SharesSnapshot | None:
        """Current shares outstanding for ``symbol``.

        ``None`` when the vendor has no usable figure for the symbol.
        """
        ...


@dataclass(frozen=True, slots=True)
class EarningsDate:
    """One upcoming (or recent) earnings event for a symbol.

    Value object at the ``clients/`` boundary (dataclass per CLAUDE.md). ``session``
    is ``"BMO"`` (before market open) / ``"AMC"`` (after market close) / ``None``
    when the vendor omits the time-of-day. Used to anchor the pre-earnings straddle
    snapshot and to window the EM-break read.
    """

    symbol: str
    date: date
    session: str | None = None
    source: str | None = None


class EarningsCalendarSource(Protocol):
    """Contract for a vendor that reports the forward earnings calendar.

    ConvexValue's ``earn_cal`` endpoint (``clients/convex_app.py``) satisfies this
    via the same pro login (no new vendor — rule 1). The keystone for the
    post-earnings EM-break / gamma-burn-off system: without earnings dates we can
    neither know a name is "post-earnings" nor snapshot the pre-earnings straddle.

    Descriptive data only (FlashAlpha rule 4).
    """

    def upcoming_earnings(self, *, days: int = 30) -> list[EarningsDate]:
        """Earnings events within ``days`` ahead, normalized to ``EarningsDate``.

        Empty list when the vendor returns nothing usable.
        """
        ...
