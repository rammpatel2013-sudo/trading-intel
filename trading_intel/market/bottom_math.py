"""Pure math for the daily brief's tape pillar and bottom checklist (2026-11-13 overnight).

Mithil's "Is it a bottom?" sheet (photo 11/13) + Tao of Trading washout /6 + follow-through
day + Elder impulse + the VIX-shock base rate (#89). Numbers in, numbers out; rule 4.
"""
from __future__ import annotations

from collections.abc import Sequence


def ema(v: Sequence[float], span: int) -> list[float]:
    out: list[float] = []
    k = 2.0 / (span + 1)
    for x in v:
        out.append(x if not out else out[-1] + k * (x - out[-1]))
    return out


def macd(v: Sequence[float]) -> tuple[list[float], list[float], list[float]]:
    """(macd line, signal, histogram) with 12/26/9."""
    e12, e26 = ema(v, 12), ema(v, 26)
    line = [a - b for a, b in zip(e12, e26)]
    sig = ema(line, 9)
    return line, sig, [a - b for a, b in zip(line, sig)]


def ppo(v: Sequence[float]) -> list[float]:
    e12, e26 = ema(v, 12), ema(v, 26)
    return [100.0 * (a - b) / b if b else 0.0 for a, b in zip(e12, e26)]


def elder(v: Sequence[float]) -> str | None:
    """Elder impulse colour of the last bar: green (EMA13 up + MACD hist up), red (both down), blue."""
    if len(v) < 40:
        return None
    e13 = ema(v, 13)
    h = macd(v)[2]
    up_e, up_h = e13[-1] > e13[-2], h[-1] > h[-2]
    return "green" if up_e and up_h else ("red" if not up_e and not up_h else "blue")


def weekly(dated: Sequence[tuple]) -> list[float]:
    """Last close of each ISO week from (date, close) pairs (oldest -> newest)."""
    weeks: dict[tuple, float] = {}
    for d, c in dated:
        weeks[d.isocalendar()[:2]] = c
    return [weeks[k] for k in sorted(weeks)]


def vix_osc(vix: Sequence[float], n: int = 10) -> list[float | None]:
    """VIX 1/10 price oscillator: % above its n-day average (fear spike = high)."""
    out: list[float | None] = []
    for i in range(len(vix)):
        if i + 1 < n:
            out.append(None)
            continue
        m = sum(vix[i + 1 - n:i + 1]) / n
        out.append(100.0 * (vix[i] / m - 1) if m else None)
    return out


def follow_through(closes: Sequence[float], vols: Sequence[float | None], *,
                   lookback: int = 25, min_gain: float = 0.0125) -> dict:
    """IBD-style rally attempt / follow-through day on the last ``lookback`` sessions.

    Low = lowest close in the window; day 1 = first up close after it; FTD = day >= 4 with a
    gain >= ``min_gain`` on higher volume than the day before (volume skipped when missing).
    A close below the low = failed (the attempt restarts from the new low next run).
    """
    n = len(closes)
    if n < 6:
        return {"state": "no data"}
    w0 = max(0, n - lookback)
    lo_i = min(range(w0, n), key=lambda i: closes[i])
    day1 = next((i for i in range(lo_i + 1, n) if closes[i] > closes[i - 1]), None)
    if day1 is None:
        return {"state": "no rally attempt", "low_idx": lo_i, "low": closes[lo_i]}
    ftd = None
    for i in range(day1 + 3, n):  # day 4 onward
        gain = closes[i] / closes[i - 1] - 1
        vol_ok = vols[i] is None or vols[i - 1] is None or (vols[i] or 0) > (vols[i - 1] or 0)
        if gain >= min_gain and vol_ok:
            ftd = i
            break
    days = n - day1
    gave_back = None
    if ftd is not None and closes[ftd] > closes[ftd - 1]:
        gave_back = (closes[ftd] - closes[-1]) / (closes[ftd] - closes[ftd - 1])
    return {"state": "follow-through" if ftd is not None else "rally attempt",
            "low_idx": lo_i, "low": closes[lo_i], "day1_idx": day1, "attempt_day": days,
            "ftd_idx": ftd, "ftd_gave_back": gave_back}


def washout(*, spy_vol: Sequence[float], up_share: Sequence[float | None],
            spx_lows: Sequence[float], vix_closes: Sequence[float],
            open_: float | None, low: float | None, close: float | None, prev_close: float | None,
            pct50: Sequence[float | None]) -> dict:
    """Tao of Trading washout score out of 6 (0 / 0.5 / 1 each; item 6 retail = no data)."""
    items: dict[str, float | None] = {}
    if len(spy_vol) >= 21:
        avg = sum(spy_vol[-21:-1]) / 20
        r = max(spy_vol[-5:]) / avg if avg else 0
        items["volume climax"] = 1.0 if r >= 3 else (0.5 if r >= 2 else 0.0)
    shares = [s for s in up_share[-10:] if s is not None]
    if shares:
        down90 = any(s <= 0.10 for s in shares)
        up90 = any(s >= 0.90 for s in shares)
        items["90% down then 90% up"] = 1.0 if (down90 and up90) else (
            0.5 if (min(shares) <= 0.12 or max(shares) >= 0.88) else 0.0)
    if len(spx_lows) >= 10 and len(vix_closes) >= 10:
        prior_low_i = min(range(len(spx_lows) - 10, len(spx_lows) - 1), key=lambda i: spx_lows[i])
        lower_low = spx_lows[-1] < spx_lows[prior_low_i]
        items["VIX divergence"] = 1.0 if (lower_low and vix_closes[-1] < vix_closes[prior_low_i]) else 0.0
    if None not in (open_, low, close, prev_close) and prev_close:
        drop = (prev_close - low) / prev_close
        items["intraday reversal"] = 1.0 if (drop >= 0.015 and close >= prev_close * 0.997) else (
            0.5 if (drop >= 0.01 and close > open_) else 0.0)
    p = [x for x in pct50[-5:] if x is not None]
    if p:
        items["% above 50-day < 20 and turning up"] = 1.0 if (min(p) < 20 and p[-1] > min(p)) else (
            0.5 if min(p) < 30 else 0.0)
    items["retail capitulation"] = None
    score = sum(v for v in items.values() if v is not None)
    return {"score": score, "items": items}


def shock_base_rate(dates: Sequence, vix: Sequence[float], spx: Sequence[float], *,
                    jump: float = 0.30, fwd: tuple[int, ...] = (5, 20)) -> dict:
    """#89: after a VIX one-day jump >= ``jump`` (+30%), SPX forward returns vs all days."""
    out: dict = {"n": 0}
    ev = [i for i in range(1, len(vix)) if vix[i - 1] and vix[i] / vix[i - 1] - 1 >= jump]
    out["n"] = len(ev)
    out["last"] = str(dates[ev[-1]]) if ev else None
    for h in fwd:
        allr = [spx[i + h] / spx[i] - 1 for i in range(len(spx) - h) if spx[i]]
        evr = [spx[i + h] / spx[i] - 1 for i in ev if i + h < len(spx) and spx[i]]
        out[f"base_{h}d"] = sum(allr) / len(allr) if allr else None
        out[f"shock_{h}d"] = sum(evr) / len(evr) if evr else None
        out[f"shock_{h}d_up"] = (sum(r > 0 for r in evr) / len(evr)) if evr else None
        out[f"shock_{h}d_n"] = len(evr)
    return out
