"""Turn one session of the position book into player EVENTS (2026-11-22). Pure logic."""

from __future__ import annotations

from datetime import date

LINE_MIN_DOI = 3000  # a single contract whose OI moved this much in one session = a "line" event
LINE_MIN_LEAN = 0.15  # ... and whose side we actually measured
PKG_MIN_LOTS = 1000


def _otm(strike: float, spot: float | None) -> float | None:
    return None if not spot else round((strike / spot - 1.0) * 100.0, 2)


def package_events(d: date, pkgs: list[dict], spot: float | None) -> list[dict]:
    out = []
    for p in pkgs:
        lots = float(p.get("lots") or 0)
        if lots < PKG_MIN_LOTS or not p.get("side"):
            continue
        out.append(
            {
                "date": d,
                "ref": p["pkg_key"],
                "structure": p["structure"],
                "cp": p["cp"],
                "expiry": p["dear_expiry"],
                "strike": float(p["dear_strike"]),
                "strike2": float(p["cheap_strike"]),
                "dte": (p["dear_expiry"] - d).days,
                "otm": _otm(float(p["dear_strike"]), spot),
                "width": abs(float(p["dear_strike"]) - float(p["cheap_strike"])),
                "lots": lots,
                "side": int(p["side"]),
                "action": p.get("action") or "unknown",
                "premium": abs(float(p.get("net") or 0)) * lots * 100.0,
                "clips": None,
                "legs": [
                    (p["dear_root"], p["dear_expiry"], float(p["dear_strike"]), p["cp"]),
                    (p["cheap_root"], p["cheap_expiry"], float(p["cheap_strike"]), p["cp"]),
                ],
            }
        )
    return out


def line_events(
    d: date, rows: list[dict], tape: dict, spot: float | None, pkg_legs: dict
) -> list[dict]:
    """Single-leg builds / unwinds: |dOI| >= LINE_MIN_DOI not explained by package legs, with a measured side."""
    out = []
    for r in rows:
        doi = r.get("d_oi") or 0.0
        if abs(doi) < LINE_MIN_DOI or abs(r.get("lean") or 0) < LINE_MIN_LEAN:
            continue
        key = r["key"]
        pk = pkg_legs.get(key, [0.0, 0.0])
        if sum(pk) >= 0.5 * abs(doi):
            continue
        t = tape.get(key, {})
        out.append(
            {
                "date": d,
                "ref": "|".join((key[0], str(key[1]), f"{key[2]:g}", key[3])),
                "structure": "single",
                "cp": key[3],
                "expiry": key[1],
                "strike": key[2],
                "strike2": None,
                "dte": (key[1] - d).days,
                "otm": _otm(key[2], spot),
                "width": None,
                "lots": abs(doi),
                "side": 1 if r["lean"] > 0 else -1,
                "action": "build" if doi > 0 else "unwind",
                "premium": t.get("premium"),
                "clips": t.get("clips"),
                "legs": [key],
            }
        )
    return out


def print_events(d: date, prints: list[dict], spot: float | None) -> list[dict]:
    out = []
    for p in prints:
        exp = p["expiry"]
        size, price = float(p["size"] or 0), float(p["price"] or 0)
        side = 1 if p.get("side") == "buy" else (-1 if p.get("side") == "sell" else 0)
        key = (p["root"], exp, float(p["strike"]), p["cp"])
        out.append(
            {
                "date": d,
                "ref": f"print|{p['ts']}|{key[0]}|{key[2]:g}{key[3]}|{size:g}",
                "structure": "single",
                "cp": key[3],
                "expiry": exp,
                "strike": key[2],
                "strike2": None,
                "dte": (exp - d).days,
                "otm": _otm(key[2], spot),
                "width": None,
                "lots": size,
                "side": side,
                "action": "print",
                "premium": size * price * 100.0,
                "clips": 1,
                "legs": [key],
            }
        )
    return out


PAIR_SIZE_TOL = 0.10  # two single-leg builds within 10% in size, same day / expiry / type ...
PAIR_MAX_WIDTH = (
    250.0  # ... and at most 250 points apart = one spread (no package data before 2026-11-05)
)


def pair_lines(events: list[dict], leans: dict) -> list[dict]:
    """Merge same-day single-leg line events that look like the two legs of one vertical. The dear leg (higher put /
    lower call strike) carries the side: customer bought the spread if its lean beats the cheap leg's by >= 0.2,
    sold if it trails by >= 0.2, else side 0 (unknown - a known player's rule may still claim it).
    """
    lines = [e for e in events if e["action"] in ("build", "unwind")]
    rest = [e for e in events if e not in lines]
    used, out = set(), []
    for i, a in enumerate(lines):
        if i in used:
            continue
        best = None
        for j in range(i + 1, len(lines)):
            b = lines[j]
            if (
                j in used
                or b["cp"] != a["cp"]
                or b["expiry"] != a["expiry"]
                or b["action"] != a["action"]
            ):
                continue
            w = abs(a["strike"] - b["strike"])
            if 0 < w <= PAIR_MAX_WIDTH and abs(a["lots"] - b["lots"]) <= PAIR_SIZE_TOL * max(
                a["lots"], b["lots"]
            ):
                if best is None or w < best[1]:
                    best = (j, w)
        if best is None:
            out.append(a)
            continue
        b = lines[best[0]]
        used.update({i, best[0]})
        dear, cheap = (a, b) if ((a["strike"] > b["strike"]) == (a["cp"] == "P")) else (b, a)
        diff = leans.get(dear["legs"][0], 0.0) - leans.get(cheap["legs"][0], 0.0)
        side = 1 if diff >= 0.2 else (-1 if diff <= -0.2 else 0)
        out.append(
            dict(
                dear,
                ref="pair|" + dear["ref"] + "|" + f"{cheap['strike']:g}",
                structure="vertical",
                strike2=cheap["strike"],
                width=best[1],
                lots=min(a["lots"], b["lots"]),
                side=side,
                premium=None,
                legs=[dear["legs"][0], cheap["legs"][0]],
            )
        )
    return out + rest
