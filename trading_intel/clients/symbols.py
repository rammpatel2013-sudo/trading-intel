"""One spelling per symbol (2026-11-19, DB wave A2 step 6; schema contract rule 8).

Our spelling: uppercase, no prefix, class shares with a dash (``SPX``, ``BRK-B``). Vendors spell the same
thing differently; convert at the client boundary with ``normalize_symbol`` (vendor -> ours) and
``vendor_symbol`` (ours -> vendor). ``symbol_map`` (table) stores the result per symbol so jobs and
readers can look a spelling up instead of re-deriving it.

    vendor     index            class share   example
    convex     SPX              BRK/B         (Convex app screener; class-share form NOT verified yet)
    cvforge    I:SPX            BRK.B         Polygon/Massive spelling
    od         SPX              BRK.B         optionsdata.ai underlying (+ iid in symbol_map.od_iid)
    fmp        ^GSPC            BRK-B
    finra      (none)           BRK/B
    yahoo      ^GSPC            BRK-B
"""

from __future__ import annotations

import re

# index -> FMP / Yahoo spelling (None = that vendor has no such index)
INDEX_FMP: dict[str, str | None] = {
    "SPX": "^GSPC",
    "NDX": "^NDX",
    "RUT": "^RUT",
    "DJX": "^DJI",
    "VIX": "^VIX",
    "VIX9D": "^VIX9D",
    "VIX3M": "^VIX3M",
    "VVIX": "^VVIX",
    "SKEW": "^SKEW",
    "OEX": "^OEX",
    "XSP": None,
    "VIX1D": "^VIX1D",
}
INDEXES = frozenset(INDEX_FMP)
_FMP_TO_OURS = {v: k for k, v in INDEX_FMP.items() if v}
_CLASS = re.compile(r"^([A-Z]{1,6})[./ -]([A-Z])$")  # BRK.B, BRK/B, BRK B, BRK-B -> BRK-B
VENDORS = ("convex", "cvforge", "od", "fmp", "finra", "yahoo")


def normalize_symbol(raw: str | None) -> str:
    """Any vendor spelling -> ours. '' for empty input. Exchange suffixes like ``BCE.TO`` are kept."""
    s = (raw or "").strip().upper()
    if not s:
        return ""
    if s.startswith("I:"):
        s = s[2:]
    if s.startswith("^"):
        s = _FMP_TO_OURS.get(s, s[1:])
    if s.startswith("$"):
        s = s[1:]
    m = _CLASS.match(s)
    return f"{m.group(1)}-{m.group(2)}" if m else s


def asset_type_of(symbol: str, is_etf: bool | None = None) -> str:
    if symbol in INDEXES:
        return "index"
    return "etf" if is_etf else "stock"


def vendor_symbol(symbol: str, vendor: str, asset_type: str | None = None) -> str | None:
    """Ours -> the vendor's spelling (None when the vendor has no such symbol)."""
    if vendor not in VENDORS:
        raise ValueError(f"unknown vendor {vendor!r}")
    s = normalize_symbol(symbol)
    idx = (asset_type or asset_type_of(s)) == "index"
    m = _CLASS.match(s)
    if idx:
        return {
            "convex": s,
            "cvforge": f"I:{s}",
            "od": s,
            "fmp": INDEX_FMP.get(s, f"^{s}"),
            "yahoo": INDEX_FMP.get(s, f"^{s}"),
            "finra": None,
        }[vendor]
    if m:
        a, b = m.group(1), m.group(2)
        return {
            "convex": f"{a}/{b}",
            "cvforge": f"{a}.{b}",
            "od": f"{a}.{b}",
            "fmp": f"{a}-{b}",
            "yahoo": f"{a}-{b}",
            "finra": f"{a}/{b}",
        }[vendor]
    return s


def map_row(symbol: str, asset_type: str) -> dict:
    """One symbol_map row (without od_iid / name)."""
    s = normalize_symbol(symbol)
    row = {"symbol": s, "asset_type": asset_type}
    for v in VENDORS:
        row[v] = vendor_symbol(s, v, asset_type)
    return row


OCC_RE = re.compile(r"^([A-Z][A-Z0-9]{0,5})\s*(\d{6})([CP])(\d{8})$")


def occ_symbol(root: str, expiry: str, cp: str, strike: float) -> str:
    """OCC 21-char key: root padded to 6, YYMMDD, C/P, strike x 1000 in 8 digits."""
    y, mo, d = expiry[:10].split("-")
    return f"{root.upper():<6}{y[2:]}{mo}{d}{cp.upper()[0]}{round(float(strike) * 1000):08d}"


def parse_occ(occ: str) -> tuple[str, str, str, float] | None:
    """OCC key -> (root, 'YYYY-MM-DD', 'C'|'P', strike); None if it does not parse."""
    m = OCC_RE.match((occ or "").strip())
    if not m:
        return None
    root, ymd, cp, k = m.groups()
    return root, f"20{ymd[:2]}-{ymd[2:4]}-{ymd[4:]}", cp, int(k) / 1000.0
