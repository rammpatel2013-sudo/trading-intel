"""Vol-surface 'footprint' read — infer dealer positioning from how a FIXED front-week
STRIKE's vol drifts day after day, then cross-check it against GEX.

The desk view this encodes: GEX is a *model/inference* of dealer positioning — useful, but
it can't see the VIX complex, single stocks or OTC, so it's a guess. The vol surface is the
receipt. If the SAME fixed call strike is offered 20-30 bp day after day while spot isn't
realising it, the street is long gamma and choking on it, trying to lighten up. A persistent
put bid = demand for downside / short downside gamma. Fixed STRIKE (not fixed delta) is the
point: a delta bucket gets smeared as spot slides along the skew, but a listed contract's
re-mark is the actual receipt. The footprint either CONFIRMS the GEX read, or tells you
something outside the index picture is overwhelming it.

Pure compute — no I/O. Descriptive read only (FlashAlpha rule 4), never a standalone signal.
"""

from __future__ import annotations

from dataclasses import dataclass

_FLAT_BP = 4.0  # |cumulative move| under this (bp) counts as flat


@dataclass(frozen=True, slots=True)
class WingDrift:
    """N-day drift of one wing's constant-delta IV (basis points)."""

    label: str
    n_days: int
    total_bp: float
    per_day_bp: float
    persistence: float  # 0..1 — fraction of daily steps in the dominant direction
    direction: str      # "offered" (marked down) | "bid" (marked up) | "flat"


@dataclass(frozen=True, slots=True)
class FootprintRead:
    call: WingDrift
    put: WingDrift
    regime: str
    gex_sign: str | None      # "long" | "short" | None
    confirms_gex: bool | None
    headline: str
    narrative: str


def _drift(label: str, ivs: list) -> WingDrift:
    """``ivs``: daily IV in DECIMAL (e.g. 0.13), oldest -> newest; ``None`` gaps dropped."""
    xs = [float(v) for v in ivs if v is not None]
    if len(xs) < 2:
        return WingDrift(label, len(xs), 0.0, 0.0, 0.0, "flat")
    total_bp = (xs[-1] - xs[0]) * 10000.0
    steps = [(xs[i] - xs[i - 1]) * 10000.0 for i in range(1, len(xs))]
    per_day = total_bp / len(steps)
    dom_up = total_bp > 0
    same = sum(1 for s in steps if s != 0 and (s > 0) == dom_up)
    persistence = same / len(steps)
    direction = "flat" if abs(total_bp) < _FLAT_BP else ("bid" if total_bp > 0 else "offered")
    return WingDrift(
        label, len(xs), round(total_bp, 1), round(per_day, 1), round(persistence, 2), direction
    )


def _persistent(w: WingDrift) -> bool:
    return w.persistence >= 0.6 and w.direction != "flat"


def _regime(call: WingDrift, put: WingDrift) -> str:
    c, p = call.direction, put.direction
    if c == "offered" and _persistent(call) and p in ("offered", "flat"):
        return "long gamma — street lightening (calls offered day after day)"
    if p == "bid" and _persistent(put) and c in ("bid", "flat"):
        return "crash-protection bid — downside demand / short downside gamma"
    if c == "bid" and p == "bid" and (_persistent(call) or _persistent(put)):
        return "short gamma — vol being bid across (event premium building)"
    if c == "offered" and p == "offered" and (_persistent(call) or _persistent(put)):
        return "long gamma — vol sold across, dealers lightening"
    return "no clean footprint yet (mixed / low persistence)"


def _fmt(w: WingDrift) -> str:
    if w.n_days < 2:
        return f"{w.label}: insufficient history"
    return (
        f"{w.label} {w.direction} {abs(w.total_bp):.0f}bp over {w.n_days}d "
        f"({w.per_day_bp:+.1f}/day, {w.persistence * 100:.0f}% one-way)"
    )


def analyze_footprint(
    *,
    call_ivs: list,
    put_ivs: list,
    net_gex: float | None = None,
    symbol: str = "SPX",
) -> FootprintRead:
    """Read the multi-day FIXED-STRIKE (front-week) vol footprint + cross-check GEX."""
    call = _drift("Front-week call strike", call_ivs)
    put = _drift("front-week put strike", put_ivs)
    regime = _regime(call, put)

    long_gamma = "long gamma" in regime
    short_gamma = "short gamma" in regime
    gex_sign = None if net_gex is None else ("long" if net_gex > 0 else "short")
    confirms: bool | None = None
    if gex_sign is not None and (long_gamma or short_gamma):
        gex_long = gex_sign == "long"
        confirms = (long_gamma and gex_long) or (short_gamma and not gex_long)

    parts = [f"{_fmt(call)}; {_fmt(put)}.", f"Read: {regime}."]
    if confirms is True:
        parts.append(f"Net GEX is {gex_sign} — the footprint CONFIRMS it.")
    elif confirms is False:
        parts.append(
            f"Net GEX reads {gex_sign}, but the surface says the opposite — something "
            "outside the index (the VIX complex, single-stock or OTC gamma) may be "
            "overwhelming the picture; weight the receipt over the model."
        )
    elif gex_sign is not None:
        parts.append(f"Net GEX is {gex_sign} (no clean footprint to confirm it yet).")
    else:
        parts.append("No GEX available to cross-check.")
    parts.append(
        "GEX is an inference of positioning; the fixed-strike vol drift is the receipt of "
        "what actually traded."
    )
    narrative = " ".join(parts)

    verb = call.direction if call.direction != "flat" else put.direction
    head = f"{regime.split(' — ')[0].capitalize()}"
    if call.n_days >= 2 and call.direction != "flat":
        head = (
            f"Front calls {call.direction} {abs(call.total_bp):.0f}bp/{call.n_days}d "
            f"({call.persistence * 100:.0f}% one-way) → {regime.split(' (')[0]}"
        )
    if gex_sign is not None and confirms is not None:
        head += f" · GEX {gex_sign} {'confirms' if confirms else 'CONTRADICTS'}"

    return FootprintRead(
        call=call, put=put, regime=regime, gex_sign=gex_sign,
        confirms_gex=confirms, headline=head, narrative=narrative,
    )
