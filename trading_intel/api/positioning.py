"""Dealer-positioning assembly for the mobile cockpit — Convex-fed DB reader.

CVForge is historical, so the LIVE cockpit does NOT pull a vendor chain. It reads
the snapshot tables the scheduler already fills from Convex — zero added vendor
calls, so the Convex 10/min cap stays reserved for the regime engine (rule 1).
Freshness = the scheduler's snapshot cadence (minutes during RTH).

Regime / GEX / DEX / flip / expected move / skew come from the proven MCP read
functions (`mcp.tools` / `mcp.extra_tools`). The **delta flip** and the **flow
cards** come off the same `greeks_snapshots` row, populated for free by the
enriched `exposures()` (delta-flip + put/call volume + delta-notional computed
from the chain `greeks_snapshot` already pulls — no extra calls). Until that
enrichment has run they read `null` and the cockpit shows them as pending.

`assemble_cockpit` is a pure mapping (unit-testable on captured samples), split
from the DB calls in `build_positioning`. Descriptor only — FlashAlpha rule 4.
"""
from __future__ import annotations

from datetime import datetime

# Cockpit GEX-by-DTE bands (calendar days, inclusive). Buckets sum to the term total.
_DTE_BUCKETS = (("0-5", 0, 5), ("5-20", 6, 20), (">20", 21, 10_000))


def _num(x):
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _bucket_gex(term):
    """Bucket a gex_term ``term`` list ([{dte, gex}, ...]) into the cockpit bands."""
    out = []
    for label, lo, hi in _DTE_BUCKETS:
        s = sum(
            _num(r.get("gex")) or 0.0
            for r in (term or [])
            if r.get("dte") is not None and lo <= r["dte"] <= hi
        )
        out.append({"bucket": label, "gex": s})
    return out


def _rr(d, key):
    """Pull a risk-reversal/ATM value from a get_skew_history payload."""
    summ = (d or {}).get("summary") or {}
    if summ.get("current_" + key) is not None:
        return _num(summ.get("current_" + key))
    rows = (d or {}).get("rows") or []
    return _num(rows[-1].get(key)) if rows else None


def _flow_from_extras(extras):
    """Flow cards from the enriched greeks_snapshots row; pending if not yet populated."""
    e = extras or {}
    cv, pv = _num(e.get("call_volume")), _num(e.get("put_volume"))
    cn, pn = _num(e.get("call_notional")), _num(e.get("put_notional"))
    if cv is None and pv is None and cn is None and pn is None:
        return {
            "pending": True, "call_volume": None, "put_volume": None,
            "pc_ratio": None, "call_notional": None, "put_notional": None,
        }
    return {
        "pending": False, "call_volume": cv, "put_volume": pv,
        "pc_ratio": (pv / cv) if (cv and cv > 0) else None,
        "call_notional": cn, "put_notional": pn,
    }


def assemble_cockpit(symbol, *, gamma_hist, gex_term, straddle, skew30, skew0, extras=None, as_of=None):
    """Map the read-function outputs (+ the enriched snapshot extras) to cockpit JSON."""
    rows = (gamma_hist or {}).get("rows") or []
    last = rows[-1] if rows else {}
    summ = (gamma_hist or {}).get("summary") or {}
    ex = extras or {}

    spot = _num(last.get("spot")) or _num((gex_term or {}).get("spot")) or _num((straddle or {}).get("spot"))
    gflip = _num(last.get("gex_flip"))
    regime_str = str(last.get("regime") or summ.get("current_regime") or "")
    if regime_str:
        short = "short" in regime_str.lower()
    else:
        gt = _num(last.get("gex_total"))
        short = None if gt is None else gt < 0

    dex_total = _num(last.get("dex_total"))
    dflip = _num(ex.get("dex_flip"))  # from the enriched snapshot row
    lean = "net long delta" if (dex_total or 0) > 0 else "net short delta" if (dex_total or 0) < 0 else "flat"

    em = None
    if straddle and straddle.get("straddle") is not None:
        pctv = _num(straddle.get("straddle_pct"))
        em = {
            "pct": (pctv / 100.0) if pctv is not None else None,
            "dollar": _num(straddle.get("straddle")),
            "lower": _num(straddle.get("lower")),
            "upper": _num(straddle.get("upper")),
            "atm_iv": _num(straddle.get("atm_iv")),
            "atm_strike": _num(straddle.get("atm_strike")),
            "dte": straddle.get("dte"),
        }

    flow = _flow_from_extras(ex)
    return {
        "symbol": symbol,
        "as_of": as_of or last.get("date") or datetime.now().isoformat(timespec="seconds"),
        "spot": spot,
        "regime": {
            "label": None if short is None else ("short gamma" if short else "long gamma"),
            "amplifying": short,
            "gex_flip": gflip,
            "dist_to_flip": ((spot - gflip) / spot) if (gflip and spot) else None,
        },
        "expected_move": em,
        "gex": {"total": _num((gex_term or {}).get("gex_total")), "near_tenor": _num(summ.get("current_gex")),
                "by_dte": _bucket_gex((gex_term or {}).get("term"))},
        "dex": {
            "total": dex_total, "flip": dflip, "lean": lean,
            "side": None if (dflip is None or not spot) else ("above delta flip" if spot >= dflip else "below delta flip"),
            "dist_to_flip": ((spot - dflip) / spot) if (dflip and spot) else None,
        },
        "flow": flow,
        "skew": {
            "rr25_30d": _rr(skew30, "rr_25d"),
            "rr10_30d": _rr(skew30, "rr_10d"),
            "rr25_0dte": _rr(skew0, "rr_25d"),
            "atm_iv": _rr(skew30, "atm_iv") or _num(last.get("atm_iv")),
        },
        "meta": {"source": "convex-db", "flow_pending": flow["pending"]},
    }


def _latest_extras(session, symbol: str) -> dict:
    """Delta-flip + flow off the newest greeks_snapshots row (nullable until enriched)."""
    from sqlalchemy import select

    from trading_intel.memory.models import GreeksSnapshot

    row = session.execute(
        select(GreeksSnapshot)
        .where(GreeksSnapshot.symbol == symbol)
        .order_by(GreeksSnapshot.ts.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        return {}
    return {
        "dex_flip": getattr(row, "dex_flip", None),
        "call_volume": getattr(row, "call_volume", None),
        "put_volume": getattr(row, "put_volume", None),
        "call_notional": getattr(row, "call_notional", None),
        "put_notional": getattr(row, "put_notional", None),
    }


def build_positioning(session, symbol: str) -> dict:
    """Assemble the cockpit payload from the Convex-fed DB (no vendor calls)."""
    from trading_intel.mcp import extra_tools as et
    from trading_intel.mcp import tools as t

    sym = symbol.upper()
    return assemble_cockpit(
        sym,
        gamma_hist=t.get_gamma_history(session, sym, days=5),
        gex_term=et.get_gex_term(session, sym),
        straddle=et.get_straddle(session, sym),
        skew30=t.get_skew_history(session, sym, horizon_dte=30),
        skew0=t.get_skew_history(session, sym, horizon_dte=1),
        extras=_latest_extras(session, sym),
    )
