"""Index-close helpers + the retired Yahoo path (Yahoo exit, step 2 - 2026-11-14).

Mithil 11/13: "we stop using yahoo"; 11/14: "why we still use yahoo we have cvforge".
Daily OHLCV bars now come from FMP through the CVForge passthrough
(``clients.fmp_prices.FmpPriceSource`` / ``fmp_price_source()``) - the ``PriceDataSource``
used by every job. This module keeps the single-value index fetchers:

- Cboe-published indices (COR1M / COR3M / VIXEQ / DSPX): Cboe CDN first, FMP second.
- Nations SDEX / VOLI / TDEX: the ONLY remaining Yahoo use. Neither FMP (CVForge) nor the
  Cboe CDN carries them (checked live 2026-11-14), and the vol-regime classifier (LEVEL /
  SKEW / TAIL) + the skew strategy read them. Every call logs ``prices.yahoo_only_index``.

All vendor calls stay in ``clients/`` (CLAUDE.md rule 1).
"""

from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)


#: Plausible value bounds for FMP / Cboe / Yahoo-only index fetches. A value outside these
#: is almost certainly a data glitch (e.g. SDEX printing 184,467,424 instead of
#: ~60). Out-of-range prints degrade to None so the z-score pipeline never
#: ingests garbage (backlog #82).
_INDEX_RANGE: dict[str, tuple[float, float]] = {
    "^SDEX": (0, 500),
    "^VOLI": (0, 200),
    "^TDEX": (0, 500),
    "^SKEW": (80, 250),
    "^COR1M": (0, 100),
    "^COR3M": (0, 100),
    "^VIXEQ": (0, 200),
    "^DSPX": (0, 200),
    # Cboe CDN keys (underscore-prefixed, same bounds).
    "_COR1M": (0, 100),
    "_COR3M": (0, 100),
    "_VIXEQ": (0, 200),
    "_DSPX": (0, 200),
}


def _range_ok(symbol: str, val: float) -> bool:
    """Return True when ``val`` is within the known plausible range for ``symbol``."""
    bounds = _INDEX_RANGE.get(symbol)
    if bounds is None:
        return True  # no range registered — pass through
    return bounds[0] <= val <= bounds[1]


def fetch_fmp_index_close(symbol: str) -> float | None:
    """Latest close for an index from FMP (CVForge passthrough), e.g. ``^COR1M``.

    Replaced the Yahoo path 2026-11-14. Degrades to ``None`` on any fetch / shape
    failure so a vendor outage never blocks the calling job. Range guard (#82) kept.
    """
    try:
        from trading_intel.clients.fmp_prices import fmp_price_source

        df = fmp_price_source().daily_history(symbol, period="5d")
    except Exception as exc:  # noqa: BLE001 - degrade to None at the vendor edge
        log.warning("fmp.index_fetch_failed", sym=symbol, error=str(exc)[:200])
        return None
    if df is None or df.empty:
        log.warning("fmp.index_empty", sym=symbol)
        return None
    try:
        val = float(df["close"].iloc[-1])
    except (TypeError, ValueError, KeyError):
        return None
    if not _range_ok(symbol, val):
        log.warning("fmp.index_out_of_range", sym=symbol, value=val, bounds=_INDEX_RANGE.get(symbol))
        return None
    return val


def fetch_cboe_index_close(cboe_sym: str, *, fmp_sym: str | None = None) -> float | None:
    """Latest close for a Cboe-published index, from the Cboe delayed-quote CDN.

    Added 2026-10-25: ``^VIXEQ`` / ``^DSPX`` / ``^COR1M`` / ``^COR3M`` went NULL
    on the Yahoo path (yfinance returns empty frames for these Cboe indices),
    which is the "VIXEQ collector" break Mithil flagged. Cboe serves all four
    on the same CDN as the VIX term structure (verified live 2026-10-25:
    VIXEQ 41.9, DSPX 36.93, COR1M 11.6, COR3M 15.0, all with full history
    CSVs). 2026-11-14: the fallback is FMP (CVForge passthrough), not Yahoo -
    FMP serves ^COR1M / ^COR3M / ^VIXEQ / ^DSPX (checked live). Returns ``None``
    when both fail.
    """
    from trading_intel.clients.cboe import CboeClient

    try:
        val = CboeClient().quote(cboe_sym)
    except Exception as exc:  # network / shape - fall through to FMP
        log.warning("cboe.index_fetch_failed", sym=cboe_sym, error=str(exc))
        val = None
    if val is not None:
        if not _range_ok(cboe_sym, val):
            log.warning("cboe.out_of_range", sym=cboe_sym, value=val,
                        bounds=_INDEX_RANGE.get(cboe_sym))
            val = None  # fall through to FMP
        else:
            return val
    if fmp_sym:
        return fetch_fmp_index_close(fmp_sym)
    return None


def _yahoo_only_index_close(symbol: str, *, period: str = "5d") -> float | None:
    """Latest close for a Nations index only Yahoo publishes (``^SDEX`` / ``^VOLI`` / ``^TDEX``).

    The last Yahoo use after the 2026-11-14 exit - logged every call so it stays visible.
    Degrades to ``None`` on any failure; range guard (#82) kept.
    """
    log.info("prices.yahoo_only_index", sym=symbol)
    try:
        import yfinance as yf
    except ImportError:  # pragma: no cover - dependency present in prod
        log.warning("prices.yfinance_missing", sym=symbol)
        return None
    try:
        raw = yf.Ticker(symbol).history(period=period, interval="1d", auto_adjust=False)
    except Exception as exc:  # noqa: BLE001 - network / shape: degrade to None
        log.warning("yf.fetch_failed", sym=symbol, error=str(exc)[:200])
        return None
    if raw is None or raw.empty or "Close" not in raw.columns:
        log.warning("yf.empty", sym=symbol)
        return None
    closes = raw["Close"].dropna()
    if closes.empty:
        return None
    try:
        val = float(closes.iloc[-1])
    except (TypeError, ValueError):
        return None
    if not _range_ok(symbol, val):
        log.warning("yf.out_of_range", sym=symbol, value=val, bounds=_INDEX_RANGE.get(symbol))
        return None
    return val


def fetch_sdex(*, period: str = "5d") -> float | None:
    """Nations SkewDex Large-Cap close (``^SDEX``) - Yahoo-only (not on FMP or the Cboe CDN)."""
    return _yahoo_only_index_close("^SDEX", period=period)


def fetch_voli(*, period: str = "5d") -> float | None:
    """Nations VolDex Large-Cap close (``^VOLI``) - Yahoo-only (not on FMP or the Cboe CDN).

    VOLI is Nations' ATM-only implied vol gauge for SPY/SPX — a cleaner
    "true IV" read than VIX (which is a strip and is sensitive to wing prices).
    """
    return _yahoo_only_index_close("^VOLI", period=period)


def fetch_tdex(*, period: str = "5d") -> float | None:
    """Nations TailDex Large-Cap close (``^TDEX``) - Yahoo-only (not on FMP or the Cboe CDN).

    TDEX normalizes the price of deep-OTM puts — pure tail-hedge demand.
    """
    return _yahoo_only_index_close("^TDEX", period=period)


def fetch_cor1m(*, period: str = "5d") -> float | None:
    """Cboe 1-month S&P 500 Implied Correlation close (``^COR1M``): Cboe CDN, then FMP.

    The implied-correlation index is the cleanest market-based dispersion gauge:
    high = index-vol-led / correlation regime (single names moving together),
    low = dispersion regime (idiosyncratic moves). Returns ``None`` on any fetch
    failure.
    """
    return fetch_cboe_index_close("_COR1M", fmp_sym="^COR1M")  # Cboe CDN first (2026-10-25)


def fetch_cor3m(*, period: str = "5d") -> float | None:
    """Cboe 3-month S&P 500 Implied Correlation close (``^COR3M``): Cboe CDN, then FMP.

    The 3-month tenor paired with ``COR1M`` gives a correlation-curve slope:
    1m > 3m (inverted) flags acute near-term correlation stress, the mirror of
    a backwardated VIX term structure.
    """
    return fetch_cboe_index_close("_COR3M", fmp_sym="^COR3M")  # Cboe CDN first (2026-10-25)


def fetch_vixeq(*, period: str = "5d") -> float | None:
    """Cboe S&P 500 Constituent Volatility Index close (``^VIXEQ``): Cboe CDN, then FMP.

    The single-stock leg of the dispersion trade: a market-cap-weighted 30-day
    implied vol of S&P 500 constituents (a "VIX of the average stock"). It sits
    far above VIX. ``VIXEQ - VIX`` is the dispersion spread; high VIXEQ with a
    flat VIX is dispersion/positioning, not a fundamental index repricing.
    History begins ~Nov 2024 (index launch). Returns ``None`` on fetch failure.
    """
    return fetch_cboe_index_close("_VIXEQ", fmp_sym="^VIXEQ")  # Cboe CDN first (2026-10-25)


def fetch_dspx(*, period: str = "5d") -> float | None:
    """Cboe S&P 500 Dispersion Index close (``^DSPX``): Cboe CDN, then FMP.

    The official 30-day implied-dispersion gauge, tied to the others by the
    identity ``DSPX^2 = VIXEQ^2 - VIX^2``. History begins ~Sep 2023 (launch).
    """
    return fetch_cboe_index_close("_DSPX", fmp_sym="^DSPX")  # Cboe CDN first (2026-10-25)
