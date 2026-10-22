"""Wider-universe + historical A-D helpers (S&P 600 rail, A-D backfill).

Split from ``market.breadth`` (400-line rule). Fetchers are best-effort over the
CVForge FMP passthrough; ``historical_ad`` / ``cumulate_ad`` are pure.
Descriptor only (rule 4).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence


def etf_holding_symbols(client: object, etf: str, *, limit: int = 700) -> list[str]:
    """Constituents of an index ETF (e.g. ``IJR`` = S&P 600) via FMP ``etf/holdings``.

    FMP has no ``sp600-constituent`` endpoint; the ETF's holdings are the practical
    universe (paid FMP tier). ``[]`` on any failure.
    """
    try:
        data = client.fmp("etf/holdings", {"symbol": etf})  # type: ignore[attr-defined]
    except Exception:
        return []
    rows = data if isinstance(data, list) else []
    syms = []
    for d in rows:
        if not isinstance(d, dict):
            continue
        sym = d.get("asset") or d.get("symbol")
        if sym and isinstance(sym, str) and sym.isascii() and sym.replace(".", "").replace("-", "").isalpha():
            syms.append(sym.upper())
    return syms[:limit]


def batch_change_counts(client: object, symbols: Sequence[str], *, chunk: int = 100) -> tuple[int, int, int]:
    """(advancers, decliners, n) from FMP ``batch-quote`` day change — ~6 calls for 600 names.

    Cheap A-D for a wide universe that does not need per-name history.
    """
    adv = dec = n = 0
    for i in range(0, len(symbols), chunk):
        part = ",".join(symbols[i:i + chunk])
        try:
            data = client.fmp("batch-quote", {"symbols": part})  # type: ignore[attr-defined]
        except Exception:
            continue
        for q in data if isinstance(data, list) else []:
            ch = q.get("change") if isinstance(q, dict) else None
            if ch is None:
                continue
            n += 1
            adv += ch > 0
            dec += ch < 0
    return adv, dec, n


def fetch_dated_closes(client: object, symbols: Sequence[str]) -> dict[str, dict]:
    """``{sym: {date: close}}`` from FMP light history (for one-off A-D backfills)."""
    from datetime import date as _date

    out: dict[str, dict] = {}
    for sym in symbols:
        try:
            data = client.fmp("historical-price-eod/light", {"symbol": sym})  # type: ignore[attr-defined]
        except Exception:
            continue
        rows = data if isinstance(data, list) else []
        series = {}
        for r in rows:
            if isinstance(r, dict) and r.get("price") is not None and r.get("date"):
                try:
                    series[_date.fromisoformat(str(r["date"])[:10])] = float(r["price"])
                except ValueError:
                    continue
        if series:
            out[sym] = series
    return out


def historical_ad(dated: Mapping[str, Mapping]) -> list[tuple]:
    """Per-session (date, advancers, decliners) from dated constituent closes. Pure.

    Survivorship caveat: uses TODAY's membership for every past day — fine for the
    A-D line's SHAPE (what the divergence read uses), not its absolute level.
    """
    days = sorted({d for s in dated.values() for d in s})
    out = []
    for prev, cur in zip(days, days[1:]):
        adv = dec = 0
        for s in dated.values():
            a, b = s.get(prev), s.get(cur)
            if a is None or b is None:
                continue
            adv += b > a
            dec += b < a
        out.append((cur, adv, dec))
    return out


def cumulate_ad(daily: Sequence[tuple], *, end_value: float | None = None) -> list[tuple]:
    """(date, adv, dec) → (date, net_adv, ad_line); shifted so the last value ==
    ``end_value`` (to splice onto an existing banked line). Pure."""
    run, out = 0.0, []
    for d, a, dcl in daily:
        run += a - dcl
        out.append((d, a - dcl, run))
    if end_value is not None and out:
        shift = end_value - out[-1][2]
        out = [(d, n, v + shift) for d, n, v in out]
    return out
