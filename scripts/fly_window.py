#!/usr/bin/env python
"""fly_window.py - 2026-11-13 (overnight run), Claude for Mithil. #60 group: butterfly window calculator.

GammaGirlLab G12 ("same money, different room"; "the window slams shut at the end"): for a long call butterfly
K-W / K / K+W bought today, how wide is the price band where it is worth at least the entry cost x (1 + target),
day by day until expiry - and how that band compares across entry DTEs for the same centre and wings.

Black-Scholes, r = 0, one flat IV (the ATM IV you pass), sticky strike - an education / planning aid, not a pricer.

    python scripts/fly_window.py --spot 6740 --center 6750 --width 25 --iv 0.15 --dte 5
    python scripts/fly_window.py ... --target 1.0      (band where the fly is worth 2x what you paid)
    python scripts/fly_window.py ... --json            (machine-readable)
Descriptive only (rule 4); no data calls.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from trading_intel.greeks.black_scholes import bs_call_price  # noqa: E402

YEAR_MIN = 365.0 * 24 * 60


def fly_value(
    spot: np.ndarray | float, center: float, width: float, t_years: float, iv: float
) -> np.ndarray:
    """Long 1 K-W call, short 2 K calls, long 1 K+W call. At expiry (t <= 0) = intrinsic."""
    s = np.asarray(spot, dtype=float)
    if t_years <= 0:
        return np.maximum(0.0, width - np.abs(s - center))
    t = np.array(t_years)
    sig = np.array(iv)

    def c(k: float) -> np.ndarray:
        return bs_call_price(s, np.array(k), sig, t)

    return c(center - width) - 2.0 * c(center) + c(center + width)


def band(
    center: float, width: float, t_years: float, iv: float, level: float, step: float = 0.25
) -> tuple[float, float] | None:
    """Lowest and highest spot where the fly is worth >= level (None if nowhere)."""
    grid = np.arange(center - 3 * width, center + 3 * width + step, step)
    v = fly_value(grid, center, width, t_years, iv)
    ok = grid[v >= level]
    return (float(ok.min()), float(ok.max())) if ok.size else None


def window(
    spot: float, center: float, width: float, iv: float, dte: int, target: float = 0.5
) -> dict:
    """Entry cost now, then for each day left (dte .. 0, at the close) the band where value >= cost x (1+target)."""
    t0 = max(dte, 0) / 365.0 if dte > 0 else 6.5 * 60 / YEAR_MIN
    cost = float(fly_value(spot, center, width, t0, iv))
    lvl = cost * (1.0 + target)
    days = []
    for d in range(dte, -1, -1):
        t = d / 365.0 if d > 0 else 0.0
        b = band(center, width, t, iv, lvl)
        days.append(
            {
                "days_left": d,
                "band": [round(b[0], 2), round(b[1], 2)] if b else None,
                "band_width": round(b[1] - b[0], 2) if b else 0.0,
                "value_at_center": round(float(fly_value(center, center, width, t, iv)), 2),
            }
        )
    return {
        "spot": spot,
        "center": center,
        "width": width,
        "iv": iv,
        "dte": dte,
        "target": target,
        "cost": round(cost, 2),
        "max_value": width,
        "max_over_cost": round(width / cost, 1) if cost > 0 else None,
        "level": round(lvl, 2),
        "days": days,
    }


def compare_dte(
    spot: float,
    center: float,
    width: float,
    iv: float,
    dtes: tuple[int, ...] = (0, 1, 2, 5, 7, 14),
    target: float = 0.5,
) -> list[dict]:
    """Same centre and wings, different entry DTE: cost, payoff multiple, and the band one day before expiry."""
    out = []
    for d in dtes:
        w = window(spot, center, width, iv, d, target)
        last = w["days"][-2] if len(w["days"]) > 1 else w["days"][-1]
        out.append(
            {
                "dte": d,
                "cost": w["cost"],
                "max_over_cost": w["max_over_cost"],
                "band_day_before": last["band"],
                "band_at_expiry": w["days"][-1]["band"],
            }
        )
    return out


def text(w: dict, cmp: list[dict]) -> str:
    L = [
        f"Fly {w['center'] - w['width']:.0f}/{w['center']:.0f}/{w['center'] + w['width']:.0f} calls, spot {w['spot']:.0f}, IV {100 * w['iv']:.1f}%, "
        f"{w['dte']} DTE: cost {w['cost']:.2f}, max {w['max_value']:.0f} ({w['max_over_cost']}x). "
        f"Band where it is worth >= {w['level']:.2f} (+{100 * w['target']:.0f}%):"
    ]
    for d in w["days"]:
        b = d["band"]
        L.append(
            f"  {d['days_left']:>2} day(s) left: "
            + (f"{b[0]:.0f} - {b[1]:.0f} ({d['band_width']:.0f} pts)" if b else "nowhere")
            + f"  · worth {d['value_at_center']:.2f} at the centre"
        )
    L.append("Same fly, different entry DTE:")
    for c in cmp:
        bb = c["band_at_expiry"]
        L.append(
            f"  {c['dte']:>2} DTE: cost {c['cost']:.2f} ({c['max_over_cost']}x max)"
            + (
                f" · at expiry worth +{100 * w['target']:.0f}% between {bb[0]:.0f} and {bb[1]:.0f}"
                if bb
                else " · never reaches the target at expiry"
            )
        )
    L.append(
        "The band narrows to the tent at expiry: the closer to expiry, the less room to be wrong (education aid)."
    )
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spot", type=float, required=True)
    ap.add_argument("--center", type=float)
    ap.add_argument("--width", type=float, default=25.0)
    ap.add_argument("--iv", type=float, default=0.15)
    ap.add_argument("--dte", type=int, default=5)
    ap.add_argument("--target", type=float, default=0.5)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    center = a.center if a.center is not None else a.spot
    w = window(a.spot, center, a.width, a.iv, a.dte, a.target)
    cmp = compare_dte(a.spot, center, a.width, a.iv, target=a.target)
    print(json.dumps({"window": w, "by_dte": cmp}, indent=1) if a.json else text(w, cmp))
    return 0


if __name__ == "__main__":
    sys.exit(main())
