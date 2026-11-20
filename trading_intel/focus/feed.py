"""Focus names -> the owner tables (2026-11-20).

One ConvexValue focus pull (decoded by scripts/cvxmap/cvx_live.decode) -> a chain frame in the chain bank's
shape -> greeks_chain rows + one greeks_snapshots row per name, source ``convex_focus``. Same maths as the
bank and the CVForge sector rows (chain_bank.add_greeks / snapshot_record), so a focus row and a bank row of
the same name read the same way; only the vendor and the cadence differ.

Vendor fields used: bid/ask mid is NOT used here (the cockpit map does its own parity-forward IV); the DB rows
carry Convex's own volatility / delta / gamma / theta / vega, day volume (volm), last price, OI.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from datetime import date, datetime, timedelta

import pandas as pd

from trading_intel import chain_bank as cb

_ROOT = re.compile(r"^\.([A-Z]+)\d")
_EPOCH = date(1970, 1, 1)


def _num(x) -> float | None:  # noqa: ANN001
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def exp_day(head) -> date | None:  # noqa: ANN001
    """Convex expiry head = days since 1970-01-01 (same as cvx_live.exp_date)."""
    try:
        return _EPOCH + timedelta(days=int(head))
    except (TypeError, ValueError):
        return None


def frame(sym: str, entry: Mapping, ref: date) -> pd.DataFrame:
    """One decoded symbol ({"spot", "exps": [(head, {strike: (call, put)})]}) -> chain bank frame
    (with vanna / charm / gxoi / dxoi / vxoi / cxoi). Settled expiries are dropped."""
    spot = _num(entry.get("spot"))
    rows: list[dict] = []
    for head, book in entry.get("exps") or []:
        ed = exp_day(head)
        if ed is None:
            continue
        for k, pair in book.items():
            for cp, x in zip(("C", "P"), pair, strict=False):
                x = x or {}
                m = _ROOT.match(str(x.get("_sym") or ""))
                rows.append({
                    "symbol": sym, "root": m.group(1) if m else "", "cp": cp,
                    "expiry": pd.Timestamp(ed), "strike": float(k),
                    "iv": _num(x.get("volatility")), "delta": _num(x.get("delta")),
                    "gamma": _num(x.get("gamma")), "theta": _num(x.get("theta")), "vega": _num(x.get("vega")),
                    "oi": _num(x.get("oi")), "volume": _num(x.get("volm")),
                    "px_close": _num(x.get("price")), "px_vwap": None, "spot": spot,
                })
    d = pd.DataFrame(rows)
    if d.empty:
        return d
    for c in ("iv", "delta", "gamma", "theta", "vega", "oi", "volume", "px_close", "px_vwap", "spot", "strike"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    iv = d["iv"][d["iv"] > 0]
    if len(iv) and iv.median() > 3:          # percent, not decimal (never seen; guarded anyway)
        d["iv"] = d["iv"] / 100.0
    return cb.add_greeks(d, ref, ref)


def records(dec: Mapping[str, Mapping], syms, ref: date, ts: datetime, stamp: datetime) -> tuple[list[dict], list[dict]]:  # noqa: ANN001
    """(greeks_chain rows, greeks_snapshots rows) for the focus names in one decoded pull."""
    chain: list[dict] = []
    snaps: list[dict] = []
    for s in syms:
        e = dec.get(s)
        if not e:
            continue
        g = frame(s, e, ref)
        if g.empty:
            continue
        chain += cb.chain_records(g, ts, stamp, source=cb.FOCUS_SOURCE)
        rec = cb.snapshot_record(g, s, ref, ts, stamp, source=cb.FOCUS_SOURCE)
        if rec:
            snaps.append(rec)
    return chain, snaps


def write(session, chain: list[dict], snaps: list[dict]) -> tuple[int, int]:  # noqa: ANN001
    """Idempotent insert into the owner tables (natural keys, ON CONFLICT DO NOTHING). Commits."""
    from trading_intel.memory.models import GreeksChain, GreeksSnapshot
    from trading_intel.scheduler.jobs.chain_bank import _insert

    n1 = _insert(session, GreeksChain, chain, ["symbol", "ts", "source", "root", "expiry", "strike", "cp"])
    n2 = _insert(session, GreeksSnapshot, snaps, ["symbol", "ts", "source"])
    session.commit()
    return n1, n2
