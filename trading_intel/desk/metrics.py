"""Desk brief metrics - pure functions (2026-11-14).

Every definition here was checked against TailThatWagsDog's 11/13 brief before it was written:
- Dark buy ratio (DBR) = FINRA short volume / (total - short) summed over 1, 5 and 10 sessions.
  Matched all 7 of his two-leg names to 2 decimals (AWK 3.15 / 2.65 / 2.35 ...).
- Buy/sell ratio (BSR) = where the close sits in the day's range: (close - low) / (high - close).
  Matched AWK 2.5, YETI 10.1, IONS 60.3.
- 1D = close vs previous close; 5D = close vs the close 5 sessions earlier (today included).
  Matched AWK +1.37 / -0.46, YETI +2.92 / -12.34, GIS +2.39 / -3.35, IONS +6.41 / -26.28.
Options bullish % and short-term gamma are OUR versions (his vendor's formulas are not public).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from statistics import median

DBR_SESSIONS = {"d1": 1, "w1": 5, "w2": 10}
BULLISH_MIN = 50.0  # options leg: > 50% of split option volume is bullish
GEX_VOL_MIN = 0.0005  # "measurable options activity": |short-term gamma / stock $ volume|


def dbr(rows: Sequence[tuple[float, float]]) -> float | None:
    """Dark buy ratio over (short, total) rows: short / (total - short)."""
    s = sum(r[0] for r in rows if r[0] is not None and r[1])
    t = sum(r[1] for r in rows if r[0] is not None and r[1])
    return s / (t - s) if t > s > 0 else None


def dark_windows(newest_first: Sequence[tuple[float, float]]) -> dict:
    """DBR 1D / 1W / 2W from the last 10 sessions (newest first), plus pass / trend."""
    out = {
        k: (dbr(newest_first[:n]) if len(newest_first) >= n else None)
        for k, n in DBR_SESSIONS.items()
    }
    w1, w2 = out["w1"], out["w2"]
    out["trend"] = (
        None if w1 is None or w2 is None else ("accumulating" if w1 >= w2 else "distributing")
    )
    out["pass"] = bool(w1 is not None and w2 is not None and w1 > 1 and w2 > 1 and w1 >= w2)
    return out


def bullish_pct(
    call_buy: float | None, call_sell: float | None, put_buy: float | None, put_sell: float | None
) -> float | None:
    """Share of side-classified option volume that is bullish: calls bought + puts sold."""
    vals = [call_buy, call_sell, put_buy, put_sell]
    if any(v is None for v in vals):
        return None
    tot = sum(vals)  # type: ignore[arg-type]
    return 100.0 * (call_buy + put_sell) / tot if tot > 0 else None  # type: ignore[operator]


def bsr(high: float | None, low: float | None, close: float | None) -> float | None:
    """(close - low) / (high - close); 99.9 when it closed on the high."""
    if None in (high, low, close) or high <= low:  # type: ignore[operator]
        return None
    if high - close <= 1e-9:  # type: ignore[operator]
        return 99.9
    return round((close - low) / (high - close), 2)  # type: ignore[operator]


def pct(a: float | None, b: float | None) -> float | None:
    return None if not a or not b else 100.0 * (a / b - 1.0)


def changes(closes: Sequence[float]) -> tuple[float | None, float | None]:
    """(1D, 5D) in % from closes oldest first (needs 6 closes for 5D)."""
    d1 = pct(closes[-1], closes[-2]) if len(closes) >= 2 else None
    d5 = pct(closes[-1], closes[-6]) if len(closes) >= 6 else None
    return d1, d5


def gex_vol(st_gex: float | None, dollar_volume: float | None) -> float | None:
    return None if st_gex is None or not dollar_volume else st_gex / dollar_volume


def med(vals: Sequence[float]) -> float | None:
    v = [x for x in vals if x is not None]
    return median(v) if v else None


def avg(vals: Sequence[float]) -> float | None:
    v = [x for x in vals if x is not None]
    return sum(v) / len(v) if v else None


# ── his 10 groups, from FMP sector + industry ──────────────────────────────
GROUPS = (
    "Health Care & Biotech",
    "Travel, Leisure & Restaurants",
    "Consumer Staples",
    "Financials",
    "Utilities & Renewables",
    "Tech & Communications",
    "Industrial & Business Services",
    "Energy & Materials",
    "Retail & Apparel",
    "Housing, Building & REITs",
)
_TRAVEL = re.compile(
    r"travel|leisure|restaurant|lodging|resort|casino|gambling|cruise|hotel|airline", re.I
)
_HOUSING = re.compile(
    r"residential construction|building|construction material|home ?build|lumber|reit", re.I
)
_PAYMENTS = re.compile(r"payment|transaction|credit services", re.I)
_MATERIALS = re.compile(r"packaging|paper|chemical|steel|metal|mining", re.I)


def group_for(sector: str | None, industry: str | None) -> str:
    """His 10 hand-made groups from FMP sector / industry (close, not exact - his map is not public)."""
    s, i = (sector or "").lower(), industry or ""
    if _TRAVEL.search(i):
        return "Travel, Leisure & Restaurants"
    if "real estate" in s or _HOUSING.search(i):
        return "Housing, Building & REITs"
    if _PAYMENTS.search(i):
        return "Financials"
    if _MATERIALS.search(i):
        return "Energy & Materials"
    for key, grp in (
        ("health", "Health Care & Biotech"),
        ("defensive", "Consumer Staples"),
        ("staples", "Consumer Staples"),
        ("financ", "Financials"),
        ("utilit", "Utilities & Renewables"),
        ("tech", "Tech & Communications"),
        ("communication", "Tech & Communications"),
        ("industr", "Industrial & Business Services"),
        ("energy", "Energy & Materials"),
        ("material", "Energy & Materials"),
        ("cyclical", "Retail & Apparel"),
        ("consumer", "Retail & Apparel"),
    ):
        if key in s:
            return grp
    return "Other"


# ── leveraged / inverse fund daily factor ──────────────────────────────────
EXTRA_LEVERAGE = (
    {  # funds the registry does not carry yet (checked against issuer names 2026-11-14)
        "PSQ": -1.0,
        "SH": -1.0,
        "DOG": -1.0,
        "RWM": -1.0,
        "SDS": -2.0,
        "QID": -2.0,
        "TWM": -2.0,
        "SPXU": -3.0,
        "SPXS": -3.0,
        "TZA": -3.0,
        "SDOW": -3.0,
        "KOLD": -2.0,
        "BOIL": 2.0,
        "SNDQ": -2.0,
        "AMDD": -1.0,
        "NBIZ": -2.0,
        "NVDD": -1.0,
        "NVDQ": -2.0,
        "TSLQ": -2.0,
        "TSLZ": -2.0,
        "LABD": -3.0,
        "FAZ": -3.0,
        "TECS": -3.0,
        "SRTY": -3.0,
        "YANG": -3.0,
    }
)


def leverage(symbol: str, name: str | None = None) -> float | None:
    """Signed daily factor: registry first, then the extra table, then the fund's name."""
    try:
        from trading_intel.etf_flows.registry import leverage_for

        lev = leverage_for(symbol)
        if lev:
            return float(lev)
    except Exception:  # registry is optional reference data; fall through to the tables below
        lev = None
    if symbol in EXTRA_LEVERAGE:
        return EXTRA_LEVERAGE[symbol]
    n = (name or "").lower()
    if not re.search(r"daily|ultra|leveraged|inverse|\bbear\b|\bbull\b|\bshort\b", n):
        return None  # a plain fund (e.g. a muni fund whose name holds a number) is not levered
    m = re.search(r"\b(\d(?:\.\d+)?)\s*x\b", n)
    size = float(m.group(1)) if m else (3.0 if "ultrapro" in n else 2.0 if "ultra" in n else None)
    short = re.search(r"\b(short|inverse|bear)\b", n) is not None
    if size is None:
        return -1.0 if short else None
    return -size if short else size
