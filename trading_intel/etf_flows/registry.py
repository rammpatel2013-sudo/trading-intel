"""Leveraged/inverse ETF reference data (leverage, issuer, underlying).

Static lookup used by the issuance descriptors to sign the daily factor ``k`` and
to bucket flow by issuer / underlying complex. Keyed to the ``LETF_SYMBOLS`` set
in ``config.py``. Reference data only — not persisted, not a vendor call.

``leverage`` is the SIGNED daily target (bull +, bear -). Index/sector factors
are stable and well established. Single-stock LETF factors have been re-struck by
their issuers (e.g. Direxion moved TSLL 1.5x->2x), so those carry ``verify=True``:
the issuance/dshares descriptors are unaffected, but confirm the factor against
the current issuer factsheet before trusting the *rebalance* estimate for them.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LetfMeta:
    """Reference row for one leveraged/inverse ETF."""

    symbol: str
    leverage: float  # signed daily factor: +3 SOXL, -3 SOXS
    underlying: str  # complex key used for bucketing (e.g. "SOX", "NDX")
    issuer: str
    name: str = ""
    verify: bool = False  # True -> confirm `leverage` before trusting rebalance $


def _m(sym: str, lev: float, und: str, iss: str, name: str, verify: bool = False) -> LetfMeta:
    return LetfMeta(sym, lev, und, iss, name, verify)


# Ordered to mirror config.LETF_SYMBOLS. Bull/bear pairs share an underlying key.
_ROWS: tuple[LetfMeta, ...] = (
    _m("TQQQ", 3.0, "NDX", "ProShares", "UltraPro QQQ"),
    _m("SQQQ", -3.0, "NDX", "ProShares", "UltraPro Short QQQ"),
    _m("SOXL", 3.0, "SOX", "Direxion", "Daily Semiconductor Bull 3X"),
    _m("SOXS", -3.0, "SOX", "Direxion", "Daily Semiconductor Bear 3X"),
    _m("SPXL", 3.0, "SPX", "Direxion", "Daily S&P 500 Bull 3X"),
    _m("SPXU", -3.0, "SPX", "ProShares", "UltraPro Short S&P 500"),
    _m("TNA", 3.0, "RUT", "Direxion", "Daily Small Cap Bull 3X"),
    _m("TZA", -3.0, "RUT", "Direxion", "Daily Small Cap Bear 3X"),
    _m("FAS", 3.0, "FIN", "Direxion", "Daily Financial Bull 3X"),
    _m("FAZ", -3.0, "FIN", "Direxion", "Daily Financial Bear 3X"),
    _m("LABU", 3.0, "BIOTECH", "Direxion", "Daily S&P Biotech Bull 3X"),
    _m("LABD", -3.0, "BIOTECH", "Direxion", "Daily S&P Biotech Bear 3X"),
    _m("NUGT", 2.0, "GOLDMINERS", "Direxion", "Daily Gold Miners Bull 2X"),
    _m("DUST", -2.0, "GOLDMINERS", "Direxion", "Daily Gold Miners Bear 2X"),
    _m("JNUG", 2.0, "JRGOLDMINERS", "Direxion", "Daily Junior Gold Miners Bull 2X"),
    _m("JDST", -2.0, "JRGOLDMINERS", "Direxion", "Daily Junior Gold Miners Bear 2X"),
    _m("BOIL", 2.0, "NATGAS", "ProShares", "Ultra Bloomberg Natural Gas"),
    _m("KOLD", -2.0, "NATGAS", "ProShares", "UltraShort Bloomberg Natural Gas"),
    _m("YINN", 3.0, "CHINA", "Direxion", "Daily FTSE China Bull 3X"),
    _m("YANG", -3.0, "CHINA", "Direxion", "Daily FTSE China Bear 3X"),
    _m("TSLL", 2.0, "TSLA", "Direxion", "Daily TSLA Bull 2X", verify=True),
    _m("TSLQ", -2.0, "TSLA", "Tradr", "2X Short TSLA Daily", verify=True),
    _m("NVDL", 2.0, "NVDA", "GraniteShares", "2x Long NVDA Daily", verify=True),
    _m("NVD", -2.0, "NVDA", "GraniteShares", "2x Short NVDA Daily", verify=True),
)

REGISTRY: dict[str, LetfMeta] = {m.symbol: m for m in _ROWS}


def meta_for(symbol: str) -> LetfMeta | None:
    """Reference row for ``symbol`` (case-insensitive); ``None`` if unregistered."""
    return REGISTRY.get(symbol.strip().upper())


def leverage_for(symbol: str) -> float | None:
    """Signed daily leverage factor for ``symbol``; ``None`` if unregistered."""
    m = meta_for(symbol)
    return m.leverage if m is not None else None
