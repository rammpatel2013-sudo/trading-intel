"""Deterministic plain-language narrative for the EOD vol report.

Turns the stored vol series into Doc-style commentary: each metric described
with its day-over-day and week-over-week move, percentile context, and a
forward "what to expect" synthesis. Pure functions over plain lists/floats —
no DB, no vendor, no LLM — so it is fully unit-testable and the MCP report tool
can never fail on an Ollama outage. Descriptive only (FlashAlpha rule 4).

Series passed in are oldest-first and may contain ``None`` gaps; helpers walk
back to the most recent real value.
"""

from __future__ import annotations

from dataclasses import dataclass

_WEEK_LAG = 5  # trading rows ≈ one week


@dataclass(frozen=True)
class Delta:
    latest: float | None
    prev: float | None
    week_ago: float | None

    @property
    def dod(self) -> float | None:
        if self.latest is None or self.prev is None:
            return None
        return self.latest - self.prev

    @property
    def dod_pct(self) -> float | None:
        if self.dod is None or not self.prev:
            return None
        return self.dod / abs(self.prev) * 100.0

    @property
    def wow(self) -> float | None:
        if self.latest is None or self.week_ago is None:
            return None
        return self.latest - self.week_ago

    @property
    def wow_pct(self) -> float | None:
        if self.wow is None or not self.week_ago:
            return None
        return self.wow / abs(self.week_ago) * 100.0


def _clean(values: list[float | None]) -> list[float]:
    return [v for v in values if v is not None]


def deltas(values: list[float | None], *, week_lag: int = _WEEK_LAG) -> Delta:
    """Latest / prior / ~week-ago real values from an oldest-first series."""
    real = _clean(values)
    if not real:
        return Delta(None, None, None)
    latest = real[-1]
    prev = real[-2] if len(real) > 1 else None
    week_ago = real[-(week_lag + 1)] if len(real) > week_lag else (real[0] if len(real) > 1 else None)
    return Delta(latest, prev, week_ago)


def _arrow(x: float | None) -> str:
    if x is None:
        return "flat"
    if x > 0:
        return "up"
    if x < 0:
        return "down"
    return "flat"


def describe(
    label: str,
    values: list[float | None],
    *,
    unit: str = "",
    dp: int = 2,
    week_lag: int = _WEEK_LAG,
) -> str:
    """One sentence: level + d/d + w/w move for ``label``."""
    d = deltas(values, week_lag=week_lag)
    if d.latest is None:
        return f"{label}: no data."
    out = f"{label} {d.latest:.{dp}f}{unit}"
    if d.dod is not None:
        pct = f" ({d.dod_pct:+.1f}%)" if d.dod_pct is not None else ""
        out += f", {_arrow(d.dod)} {abs(d.dod):.{dp}f}{pct} d/d"
    if d.wow is not None:
        out += f"; {_arrow(d.wow)} {abs(d.wow):.{dp}f} vs a week ago"
    return out + "."


def pctile_phrase(pctile: float | None, *, high_is: str = "rich", low_is: str = "cheap") -> str:
    """Percentile (0..1) → words."""
    if pctile is None:
        return ""
    p = pctile * 100.0
    if p >= 90:
        return f"{high_is} — {p:.0f}th pctile, near the top of its year"
    if p >= 70:
        return f"elevated ({p:.0f}th pctile)"
    if p <= 10:
        return f"{low_is} — {p:.0f}th pctile, near the floor"
    if p <= 30:
        return f"below average ({p:.0f}th pctile)"
    return f"mid-range ({p:.0f}th pctile)"


def term_phrase(vix9d: float | None, vix: float | None, vix3m: float | None) -> str:
    """Front-end and curve-shape read."""
    bits: list[str] = []
    if vix9d is not None and vix is not None:
        spread = vix9d - vix
        if spread > 0.25:
            bits.append(
                f"front inverted — VIX9D ({vix9d:.2f}) above VIX ({vix:.2f}), "
                f"spread {spread:+.2f}: acute near-term stress, but also unspent "
                f"crush fuel if calm holds"
            )
        elif spread < -0.25:
            bits.append(
                f"front in normal slope — VIX9D ({vix9d:.2f}) below VIX ({vix:.2f}): "
                f"no near-term-stress premium left to bleed off"
            )
        else:
            bits.append(f"front roughly flat (VIX9D≈VIX, {spread:+.2f})")
    if vix is not None and vix3m is not None:
        if vix3m > vix + 0.25:
            bits.append("back in contango (30D < 3M) — market pricing this as transitory")
        elif vix3m < vix - 0.25:
            bits.append("back inverted (30D > 3M) — sustained-stress pricing")
    return "; ".join(bits) + ("." if bits else "")


def dispersion_phrase(
    *,
    cor1m: float | None,
    cor3m: float | None,
    vixeq: float | None,
    vix: float | None,
    spread: float | None,
    spread_dod: float | None,
    cor1m_pctile: float | None,
) -> str:
    """Correlation / dispersion read in Doc's framing."""
    bits: list[str] = []
    if cor1m is not None:
        ctx = pctile_phrase(cor1m_pctile, high_is="stretched", low_is="near the floor")
        bits.append(f"COR1M {cor1m:.2f}" + (f" ({ctx})" if ctx else ""))
    if cor1m is not None and cor3m is not None:
        slope = cor1m - cor3m
        if slope > 0.5:
            bits.append(
                f"1M>3M (slope {slope:+.2f}) — near-term correlation stress, the "
                f"correlation analogue of a backwardated VIX curve"
            )
        elif slope < -0.5:
            bits.append(f"1M<3M (slope {slope:+.2f}) — correlation curve normal/upward")
    if spread is not None:
        widen = ""
        if spread_dod is not None:
            widen = " (widening)" if spread_dod > 0 else " (narrowing)" if spread_dod < 0 else ""
        bits.append(
            f"VIXEQ−VIX dispersion spread {spread:.2f}{widen}"
            + (f" — single-stock vol {vixeq:.1f} vs index {vix:.1f}" if (vixeq and vix) else "")
        )
        if spread_dod is not None and spread_dod > 0:
            bits.append(
                "spread re-widening as index vol falls is the dispersion-desk tell: "
                "selling index vol against flat single-stock vol — calm that rebuilds "
                "the same coiled spring"
            )
    return ". ".join(bits) + ("." if bits else "")


def forward_bullets(ctx: dict) -> list[str]:
    """Rule-based 'what to expect next day / next week' from the current state."""
    out: list[str] = []
    vix = ctx.get("vix")
    vix9d = ctx.get("vix9d")
    tail_pctile = ctx.get("tail_pctile")
    cor1m = ctx.get("cor1m")
    cor3m = ctx.get("cor3m")
    spread_dod = ctx.get("spread_dod")
    vol_falling = ctx.get("vol_falling")
    catalyst = ctx.get("catalyst")  # (label, dte)

    if vix9d is not None and vix is not None and (vix9d - vix) > 0.25 and vol_falling:
        out.append(
            "Front-end inversion + falling vol = unspent crush fuel: vol can grind "
            "lower over the next few sessions without any new good news, simply as the "
            "9-day premium reverts."
        )
    elif vix9d is not None and vix is not None and (vix9d - vix) <= 0.25:
        out.append(
            "Front-end inversion already gone — the easy mechanical crush is spent; "
            "further downside in vol now needs realized vol to keep falling."
        )

    if tail_pctile is not None and tail_pctile <= 0.20:
        cat = f" into {catalyst[0]} ({catalyst[1]}d)" if catalyst else ""
        out.append(
            f"Tail/skew protection is back near the floor — cheap to own downside "
            f"hedges{cat}; an asymmetric place to add protection rather than chase."
        )

    if cor1m is not None and cor3m is not None and (cor1m - cor3m) > 0.5:
        out.append(
            "Correlation still inverted (1M>3M): the index stays headline-sensitive — "
            "expect outsized index moves on the next macro print even if single names are quiet."
        )

    if spread_dod is not None and spread_dod > 0 and vol_falling:
        out.append(
            "Dispersion spread widening into the calm = the same index-vol-selling trade "
            "reloading at better levels; fragility rebuilds underneath — a sharp re-snap "
            "on a headline is the tail risk next week."
        )

    if catalyst:
        out.append(
            f"Dated catalyst: {catalyst[0]} in {catalyst[1]}d — big open interest rolls "
            f"off and the gamma map resets; expect tighter ranges into it, freer moves after."
        )

    if not out:
        out.append("No strong directional tells in the current vol state — neutral.")
    return out
