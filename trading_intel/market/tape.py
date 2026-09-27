"""Tape (volume / money-flow) descriptors from daily OHLCV — pure math.

Scope per Mithil (2026-09-27): volume vs its 20-day average, OBV, CMF, VPT, an
anchored VWAP and a volume profile (POC + 70% value area). No other technicals.
Numbers-in → numbers-out; descriptor only (CLAUDE.md rule 4).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Bar:
    high: float
    low: float
    close: float
    volume: float


def rel_volume(bars: Sequence[Bar], n: int = 20) -> float | None:
    """Last session's volume ÷ the average of the prior ``n`` sessions."""
    if len(bars) < n + 1:
        return None
    avg = sum(b.volume for b in bars[-n - 1:-1]) / n
    return bars[-1].volume / avg if avg else None


def obv(bars: Sequence[Bar]) -> list[float]:
    """On-balance volume (cumulative, starts at 0)."""
    out, v = [], 0.0
    for i, b in enumerate(bars):
        if i:
            prev = bars[i - 1].close
            v += b.volume if b.close > prev else -b.volume if b.close < prev else 0.0
        out.append(v)
    return out


def vpt(bars: Sequence[Bar]) -> list[float]:
    """Volume-price trend: cumulative volume × % close change."""
    out, v = [], 0.0
    for i, b in enumerate(bars):
        if i and bars[i - 1].close:
            v += b.volume * (b.close / bars[i - 1].close - 1.0)
        out.append(v)
    return out


def cmf(bars: Sequence[Bar], n: int = 20) -> float | None:
    """Chaikin money flow over the last ``n`` sessions (−1..+1)."""
    if len(bars) < n:
        return None
    mfv = vol = 0.0
    for b in bars[-n:]:
        rng = b.high - b.low
        mult = ((b.close - b.low) - (b.high - b.close)) / rng if rng > 0 else 0.0
        mfv += mult * b.volume
        vol += b.volume
    return mfv / vol if vol else None


def anchored_vwap(bars: Sequence[Bar]) -> float | None:
    """VWAP of the typical price over ``bars`` (anchor = first bar)."""
    num = sum((b.high + b.low + b.close) / 3 * b.volume for b in bars)
    den = sum(b.volume for b in bars)
    return num / den if den else None


def volume_profile(bars: Sequence[Bar], *, bins: int = 24, value_area: float = 0.70
                   ) -> dict[str, object] | None:
    """Daily-bar volume profile: each bar's volume spread evenly over its high–low.

    Returns ``{edges, volume, poc, val, vah}`` — POC = the highest-volume bin
    centre; VAL/VAH = the ``value_area`` share of volume grown outward from the POC.
    """
    if not bars:
        return None
    lo, hi = min(b.low for b in bars), max(b.high for b in bars)
    if hi <= lo:
        return None
    w = (hi - lo) / bins
    vol = [0.0] * bins
    for b in bars:
        a = int((b.low - lo) / w)
        z = min(bins - 1, int((b.high - lo) / w))
        a = min(a, bins - 1)
        per = b.volume / (z - a + 1)
        for k in range(a, z + 1):
            vol[k] += per
    poc_i = max(range(bins), key=lambda k: vol[k])
    total, acc = sum(vol), vol[poc_i]
    left = right = poc_i
    while total and acc / total < value_area and (left > 0 or right < bins - 1):
        up = vol[right + 1] if right < bins - 1 else -1.0
        dn = vol[left - 1] if left > 0 else -1.0
        if up >= dn:
            right += 1
            acc += vol[right]
        else:
            left -= 1
            acc += vol[left]
    edges = [lo + w * k for k in range(bins + 1)]
    return {"edges": edges, "volume": vol, "poc": lo + w * (poc_i + 0.5),
            "val": edges[left], "vah": edges[right + 1]}


def slope(series: Sequence[float], n: int = 20) -> float | None:
    """Simple change over the last ``n`` points (sign = direction of accumulation)."""
    if len(series) < n + 1:
        return None
    return series[-1] - series[-1 - n]
