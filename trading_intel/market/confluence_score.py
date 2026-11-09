"""Forward test for the confluence read-out — pure scoring over stored days (no DB).

For each stored day's zones, look at the next 1 and 5 sessions' SPX daily bars:

* **touched** — some bar's high/low range reached the zone band (the zone's low..high,
  widened to at least ``min_band`` points around the centre).
* **held** — touched, and the horizon's last close is back on the zone's ORIGINAL side
  of its centre (above it for a zone that started below spot, below it for one above).
* **broke** — touched and the last close finished on the other side.

Key zones (3+ layers) are compared with single-layer levels. A zone that contains the
day's spot has no original side and is skipped; zones further than ``max_dist_pct``
from spot are skipped too (the horizon cannot reasonably reach them).

The output is a track record of a DESCRIPTOR, not a trading result (rule 4). With
fewer than 30 zones in a group the rates are noise and the report says so.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

from trading_intel.market.confluence import KEY_MIN_LAYERS
from trading_intel.market.confluence_layers import Bar

HORIZONS: tuple[int, ...] = (1, 5)
MIN_SAMPLE = 30


def score_zone(
    zone: dict, spot: float, future: Sequence[Bar], *, min_band: float = 10.0
) -> dict | None:
    """Score one zone over ``future`` bars (already cut to the horizon). None = not scorable."""
    if not future:
        return None
    c = float(zone["centre"])
    lo = min(float(zone["low"]), c - min_band / 2)
    hi = max(float(zone["high"]), c + min_band / 2)
    if lo <= spot <= hi:
        return None
    below = c < spot  # zone started below spot -> original side is "above"
    touched = any(b.low <= hi and b.high >= lo for b in future)
    last = future[-1].close
    held = touched and ((last > c) if below else (last < c))
    return {"touched": touched, "held": held, "broke": touched and not held}


def _rates(rows: list[dict]) -> dict[str, Any]:
    n = len(rows)
    t = sum(r["touched"] for r in rows)
    h = sum(r["held"] for r in rows)
    return {
        "n": n, "touched": t, "held": h, "broke": t - h,
        "touch_rate": round(t / n, 3) if n else None,
        "hold_rate": round(h / t, 3) if t else None,
        "small_sample": n < MIN_SAMPLE,
    }


def score_days(days: Sequence[dict], bars: Sequence[Bar], *, max_dist_pct: float = 0.05,
               horizons: Sequence[int] = HORIZONS) -> dict[str, Any]:
    """Score every stored read-out in ``days`` against later ``bars``.

    ``days`` are read-out dicts (``as_of``, ``spot``, ``zones``). Returns per horizon the
    key-zone and single-layer groups (counts + touch / hold rates) and how many days were
    scored. Days without enough later bars for a horizon are left out of that horizon.
    """
    b = sorted(bars, key=lambda x: x.d)
    out: dict[str, Any] = {"horizons": {}, "days_scored": {}, "min_sample": MIN_SAMPLE}
    for hz in horizons:
        groups: dict[str, list[dict]] = {"key": [], "single": [], "multi2": []}
        nd = 0
        for day in days:
            d = date.fromisoformat(str(day["as_of"])[:10])
            spot = float(day["spot"])
            fut = [x for x in b if x.d > d][:hz]
            if len(fut) < hz:
                continue
            nd += 1
            for z in day.get("zones", []):
                if abs(float(z["centre"]) - spot) > spot * max_dist_pct:
                    continue
                s = score_zone(z, spot, fut)
                if s is None:
                    continue
                n = int(z.get("n_layers", 0))
                g = "key" if n >= KEY_MIN_LAYERS else "single" if n == 1 else "multi2"
                groups[g].append(s)
        out["horizons"][str(hz)] = {g: _rates(v) for g, v in groups.items()}
        out["days_scored"][str(hz)] = nd
    return out


def headline(score: dict) -> str:
    """One plain sentence for the report: key-zone vs single-layer hold rate (5-session)."""
    hz = score.get("horizons", {}).get("5") or score.get("horizons", {}).get("1")
    if not hz:
        return "Track record so far: no scored days yet (needs later sessions)."
    k, s = hz["key"], hz["single"]

    def pct(v: float | None) -> str:
        return "n/a" if v is None else f"{v * 100:.0f}%"

    line = (f"Track record so far: {k.get('n', 0)} key zones, key-zone hold "
            f"{pct(k.get('hold_rate'))} vs single-layer {pct(s.get('hold_rate'))} "
            f"({s.get('n', 0)} single-layer levels).")
    if k.get("small_sample", (k.get("n") or 0) < MIN_SAMPLE) or s.get(
            "small_sample", (s.get("n") or 0) < MIN_SAMPLE):
        line += f" Fewer than {MIN_SAMPLE} in a group: too few to mean anything yet."
    return line
