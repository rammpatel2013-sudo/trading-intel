"""Index membership intervals - S&P 500 point-in-time, S&P 400 / 600 as of today. Pure.

S&P 500: today's list walked BACKWARD through FMP's change log, so a name that was dropped
in 2024 is still a member on 2024's dates (no survivorship bias in history built on it).
A same-company ticker change (removed and added security have the same name, e.g. DWDP -> DD)
is not a membership change and is skipped.

S&P 400 / 600: FMP has no change log, so today's IJH / IJR holdings are used for every date
(``source`` = ``ijh_today`` / ``ijr_today``) - survivorship-biased, flagged wherever it matters.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

BENCHMARKS = ("SPY", "RSP", "QQQ", "IWM", "MDY", "IJR")


@dataclass
class Interval:
    symbol: str
    index_code: str
    start_date: date | None
    end_date: date | None
    source: str

    def contains(self, d: date) -> bool:
        return (self.start_date is None or self.start_date <= d) and (
            self.end_date is None or d < self.end_date
        )


def norm_symbol(sym: str | None) -> str:
    """FMP spelling: upper case, class shares with a dash (BRK.B -> BRK-B)."""
    return (sym or "").strip().upper().replace(".", "-")


def _iso(s: object) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def _same_company(row: Mapping) -> bool:
    a = str(row.get("addedSecurity") or "").strip().lower()
    r = str(row.get("removedSecurity") or "").strip().lower()
    return bool(a) and a == r


def sp500_intervals(
    current: Sequence[Mapping], changes: Sequence[Mapping], *, since: date = date(2018, 1, 1)
) -> list[Interval]:
    """Membership intervals from today's list + the change log (newest -> oldest)."""
    open_: dict[str, Interval] = {}
    done: list[Interval] = []
    for row in current:
        sym = norm_symbol(row.get("symbol"))
        if sym:
            open_[sym] = Interval(sym, "SP500", None, None, "fmp_sp500_pit")
    rows = [r for r in changes if _iso(r.get("date"))]
    rows.sort(key=lambda r: _iso(r.get("date")), reverse=True)  # type: ignore[arg-type, return-value]
    for row in rows:
        d = _iso(row.get("date"))
        if d is None or d < since:
            break
        if _same_company(row):
            continue
        added = norm_symbol(row.get("addedTicker"))
        removed = norm_symbol(row.get("removedTicker"))
        if added and added in open_:
            iv = open_.pop(added)
            iv.start_date = d
            done.append(iv)
        if removed and removed not in open_:
            open_[removed] = Interval(removed, "SP500", None, d, "fmp_sp500_pit")
    done.extend(open_.values())
    return sorted(done, key=lambda iv: (iv.symbol, iv.start_date or date.min))


def etf_intervals(holdings: Sequence[Mapping], index_code: str, etf: str) -> list[Interval]:
    """Today's ETF holdings as open intervals (common-stock tickers only)."""
    out: dict[str, Interval] = {}
    for h in holdings:
        sym = norm_symbol(h.get("asset") or "")
        if not sym or not sym.replace("-", "").isalpha() or len(sym) > 6:
            continue
        out[sym] = Interval(sym, index_code, None, None, f"{etf.lower()}_today")
    return list(out.values())


def members_on(
    intervals: Iterable[Interval], d: date, *, codes: Iterable[str] | None = None
) -> set[str]:
    want = set(codes) if codes else None
    return {
        iv.symbol for iv in intervals if (want is None or iv.index_code in want) and iv.contains(d)
    }


def all_symbols(intervals: Iterable[Interval], *, since: date | None = None) -> list[str]:
    """Every symbol that was a member at any time on/after ``since`` (sorted)."""
    syms = {
        iv.symbol for iv in intervals if since is None or iv.end_date is None or iv.end_date > since
    }
    return sorted(syms)
