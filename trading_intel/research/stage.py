"""Weinstein stage analysis from OHLC bars (weekly / daily / 4-hour).

Pure classifier (`classify`) + a thin CVForge puller (`stages_from_cvforge`) that pulls
aggs at the three timeframes and classifies each. The stage logic:

    Stage 1  base / accumulation  — price below a FLATTENING long MA (down-move spent)
    Stage 2  advance / markup     — price above a RISING long MA
    Stage 3  top / distribution   — price above a FLATTENING/rolling long MA
    Stage 4  decline / markdown   — price below a FALLING long MA

Weinstein's anchor is the 30-WEEK MA; daily uses ~150-day (~30 weeks); 4h a proportional
~150-period. Descriptive technical read only (rule 4).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

_LABEL = {
    1: "Basing (accumulation)",
    2: "Advancing (markup)",
    3: "Topping (distribution)",
    4: "Declining (markdown)",
}
_ACTION = {
    1: "watch for a breakout above a flattening MA",
    2: "up-trend — hold / add on pullbacks",
    3: "tighten stops / trim into strength",
    4: "avoid longs — down-trend intact",
}

#: timeframe -> (aggs multiplier, aggs timespan, MA window in bars)
TIMEFRAMES: dict[str, tuple[int, str, int]] = {
    "weekly": (1, "week", 30),
    "daily": (1, "day", 150),
    "4h": (4, "hour", 150),
}


@dataclass(frozen=True, slots=True)
class StageRead:
    """One timeframe's stage classification."""

    timeframe: str
    stage: str  # "Stage 1".."Stage 4"
    label: str  # Basing / Advancing / Topping / Declining
    above_ma: bool
    ma_slope: float  # long-MA change over the slope lookback (price units)
    last: float
    ma: float
    action: str


def sma(closes: Sequence[float], window: int) -> list[float | None]:
    """Trailing simple moving average; ``None`` until ``window`` bars exist."""
    out: list[float | None] = []
    for i in range(len(closes)):
        if i + 1 < window:
            out.append(None)
        else:
            out.append(sum(closes[i + 1 - window : i + 1]) / window)
    return out


def classify(
    closes: Sequence[float],
    *,
    ma_window: int,
    slope_lookback: int = 5,
    long_lookback: int | None = None,
) -> StageRead | None:
    """Classify the newest bar's Weinstein stage. ``None`` if too few bars.

    Price-vs-MA + short MA slope separates advancing (2) from declining (4). The
    base/top ambiguity (both sit near a flat MA) is resolved with the MA's LONGER
    trend (``long_lookback``, default one MA window): a down/flat long-trend means we
    came from a decline -> Stage 1 base; an up long-trend means we came from an
    advance -> Stage 3 top.
    """
    n = len(closes)
    if n < ma_window + slope_lookback:
        return None
    ma = sma(closes, ma_window)
    last = float(closes[-1])
    m = ma[-1]
    m_prev = ma[-1 - slope_lookback]
    if m is None or m_prev is None:
        return None
    slope = float(m) - float(m_prev)
    above = last >= m
    llb = long_lookback if long_lookback is not None else ma_window
    m_long = ma[-1 - llb] if 0 < llb < len(ma) and ma[-1 - llb] is not None else m_prev
    long_trend = float(m) - float(m_long)
    if above and slope > 0:
        code = 2  # advancing / markup
    elif not above and slope < 0:
        code = 4  # declining / markdown
    elif long_trend <= 0:
        code = 1  # basing — broader MA trend down/flat (came from a decline)
    else:
        code = 3  # topping — broader MA trend up (came from an advance)
    return StageRead("", f"Stage {code}", _LABEL[code], above, slope, last, float(m), _ACTION[code])


def stages_from_cvforge(client: object, symbol: str, *, frm: str, to: str) -> dict[str, StageRead]:
    """Pull CVForge aggs at weekly/daily/4h and classify each. I/O (runs on the box).

    ``client`` is a ``CVForgeClient``; ``frm``/``to`` are ISO dates bounding the window
    (use a wide window for weekly, e.g. 4 y, so the 30-week MA has history).
    """
    out: dict[str, StageRead] = {}
    for tf, (mult, span, maw) in TIMEFRAMES.items():
        df = client.aggs(symbol, frm=frm, to=to, multiplier=mult, timespan=span, limit=50000)  # type: ignore[attr-defined]
        closes = [float(c) for c in df["c"].tolist()] if not df.empty else []
        read = classify(closes, ma_window=maw)
        if read is not None:
            out[tf] = StageRead(
                tf,
                read.stage,
                read.label,
                read.above_ma,
                read.ma_slope,
                read.last,
                read.ma,
                read.action,
            )
    return out
