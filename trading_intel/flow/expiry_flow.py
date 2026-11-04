"""Expiry-flow term view (Phase 5 E1, 2026-11-04) — pure, no I/O.

Where in the term structure is the money going? Three reads per days-to-expiry
bucket (0-7 / 8-30 / 31-60 / 60+ days):

  premium      bullish $ (calls bought + puts sold) vs bearish $ (puts bought +
               calls sold) from the tape roll-up ``tas_daily_contract``
  open int.    call vs put open interest from the latest EOD chain
  demand/write open-interest change paired with the contract's IV change
               (OI alone gives opening vs closing, never buy vs sell —
               see MEMORY oi-flow-direction):
                 OI up  + IV up   -> opened by BUYERS   (demand-led)
                 OI up  + IV down -> opened by WRITERS  (supply-led)
                 OI down + IV down -> longs closing
                 OI down + IV up   -> shorts covering
               weighted by |OI change| contracts.
Descriptor only (rule 4).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

BUCKETS: tuple[tuple[int, int, str], ...] = (
    (0, 7, "0–7d"),
    (8, 30, "8–30d"),
    (31, 60, "31–60d"),
    (61, 100000, "60d+"),
)
IV_EPS = 0.0005  # IV change smaller than 0.05 vol pt = no read


def bucket_of(dte: int) -> str | None:
    for lo, hi, name in BUCKETS:
        if lo <= dte <= hi:
            return name
    return None


def _blank() -> dict:
    return {name: {} for _, _, name in BUCKETS}


def premium_by_bucket(
    rows: Iterable[tuple[date, date | None, str | None, float | None, float | None]],
) -> dict[str, dict]:
    """rows: (trade_date, expiry, cp, buy_notional, sell_notional)."""
    out = {
        name: {
            "bull": 0.0,
            "bear": 0.0,
            "call_buy": 0.0,
            "call_sell": 0.0,
            "put_buy": 0.0,
            "put_sell": 0.0,
        }
        for _, _, name in BUCKETS
    }
    for td, exp, cp, buy, sell in rows:
        if exp is None or cp not in ("C", "P"):
            continue
        b = bucket_of((exp - td).days)
        if b is None:
            continue
        buy, sell = float(buy or 0.0), float(sell or 0.0)
        o = out[b]
        if cp == "C":
            o["call_buy"] += buy
            o["call_sell"] += sell
            o["bull"] += buy
            o["bear"] += sell
        else:
            o["put_buy"] += buy
            o["put_sell"] += sell
            o["bear"] += buy
            o["bull"] += sell
    for o in out.values():
        o["net"] = o["bull"] - o["bear"]
        tot = o["bull"] + o["bear"]
        o["bull_share"] = round(o["bull"] / tot, 3) if tot else None
    return out


def oi_by_bucket(rows: Iterable[tuple[int, str, int | None]]) -> dict[str, dict]:
    """rows: (dte, cp, oi)."""
    out = {name: {"call_oi": 0, "put_oi": 0} for _, _, name in BUCKETS}
    for dte, cp, oi in rows:
        b = bucket_of(int(dte))
        if b is None or not oi:
            continue
        out[b]["call_oi" if cp == "C" else "put_oi"] += int(oi)
    for o in out.values():
        o["pc_ratio"] = round(o["put_oi"] / o["call_oi"], 2) if o["call_oi"] else None
    return out


QUADS = ("bought_open", "written_open", "long_close", "short_cover")


def demand_write(
    rows: Iterable[tuple[int, str, int | None, float | None, float | None]],
) -> dict[str, dict]:
    """rows: (dte, cp, oi_change, iv_now, iv_prev) per contract. Contracts, by quadrant."""
    out = {
        name: {f"{side}_{q}": 0 for side in ("call", "put") for q in QUADS} | {"no_read": 0}
        for _, _, name in BUCKETS
    }
    for dte, cp, doi, iv1, iv0 in rows:
        b = bucket_of(int(dte))
        if b is None or not doi:
            continue
        side = "call" if cp == "C" else "put"
        if iv1 is None or iv0 is None or abs(iv1 - iv0) < IV_EPS:
            out[b]["no_read"] += abs(int(doi))
            continue
        up_oi, up_iv = doi > 0, iv1 > iv0
        q = (
            ("bought_open" if up_iv else "written_open")
            if up_oi
            else ("short_cover" if up_iv else "long_close")
        )
        out[b][f"{side}_{q}"] += abs(int(doi))
    for o in out.values():
        for side in ("call", "put"):
            opened = o[f"{side}_bought_open"] + o[f"{side}_written_open"]
            o[f"{side}_demand_share"] = (
                round(o[f"{side}_bought_open"] / opened, 3) if opened else None
            )
    return out


def _money(x: float) -> str:
    a = abs(x)
    s = f"${a / 1e9:.2f}bn" if a >= 1e9 else f"${a / 1e6:.1f}m" if a >= 1e6 else f"${a / 1e3:.0f}k"
    return ("−" if x < 0 else "") + s


def plain_read(prem: dict[str, dict], dw: dict[str, dict]) -> list[str]:
    """One sentence per bucket that has anything to say."""
    lines = []
    for _, _, name in BUCKETS:
        p, d = prem.get(name, {}), dw.get(name, {})
        tot = (p.get("bull") or 0) + (p.get("bear") or 0)
        bits = []
        if tot:
            lean = "bullish" if p["net"] > 0 else "bearish"
            bits.append(
                f"{_money(tot)} traded, net {lean} {_money(abs(p['net']))} "
                f"({(p['bull_share'] or 0) * 100:.0f}% bullish)"
            )
        for side in ("call", "put"):
            sh = d.get(f"{side}_demand_share")
            if sh is not None:
                who = "buyers" if sh >= 0.5 else "writers"
                bits.append(f"new {side}s mostly opened by {who} ({sh * 100:.0f}% bought)")
        if bits:
            lines.append(f"{name}: " + "; ".join(bits) + ".")
    return lines
