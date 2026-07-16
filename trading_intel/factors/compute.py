"""Multi-factor cross-sectional scoring (Growth / Quality / Value / Momentum / Risk).

Pure compute: given per-name fundamental + momentum inputs for a universe, produce
cross-sectional z-scores per factor and a weighted composite. Every factor is
oriented so **higher = more attractive** (Value: cheaper; Quality: better returns
on capital / margins, less leverage; Growth: faster; Momentum: stronger trailing
return; Risk: more defensive — low beta/leverage, ample liquidity).

Scores are RELATIVE to the scanned universe (a z-score is only meaningful vs
peers), matching the "cross-sectional rank interim / percentiles bank forward"
approach used elsewhere. No I/O — the FMP pull + persistence live in
``scheduler/jobs/factor_scores.py``; this module is unit-tested standalone.

Descriptive research scores only (FlashAlpha rule 4) — never a standalone signal.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields

import numpy as np

# metric -> orientation (+1 higher-better, -1 lower-better), grouped by factor.
FACTOR_DEFS: dict[str, tuple[tuple[str, int], ...]] = {
    "value": (("pe", -1), ("pb", -1), ("ps", -1), ("ev_ebitda", -1)),
    "quality": (
        ("roe", 1),
        ("roic", 1),
        ("gross_margin", 1),
        ("net_margin", 1),
        ("fcf_margin", 1),
        ("debt_to_equity", -1),
    ),
    "growth": (("revenue_growth", 1), ("eps_growth", 1)),
    "momentum": (("ret_12m", 1), ("ret_3m", 1)),
    "risk": (("beta", -1), ("debt_to_equity", -1), ("current_ratio", 1)),
}
FACTORS: tuple[str, ...] = tuple(FACTOR_DEFS)
DEFAULT_WEIGHTS: dict[str, float] = {f: 1.0 / len(FACTORS) for f in FACTORS}


@dataclass(frozen=True, slots=True)
class FactorInputs:
    """Raw per-name factor inputs (all optional; missing metrics are skipped)."""

    symbol: str
    pe: float | None = None
    pb: float | None = None
    ps: float | None = None
    ev_ebitda: float | None = None
    roe: float | None = None
    roic: float | None = None
    gross_margin: float | None = None
    net_margin: float | None = None
    fcf_margin: float | None = None
    debt_to_equity: float | None = None
    current_ratio: float | None = None
    revenue_growth: float | None = None
    eps_growth: float | None = None
    beta: float | None = None
    ret_3m: float | None = None
    ret_12m: float | None = None


@dataclass(frozen=True, slots=True)
class FactorScores:
    """Cross-sectional factor z-scores + weighted composite for one name."""

    symbol: str
    value: float | None
    quality: float | None
    growth: float | None
    momentum: float | None
    risk: float | None
    composite: float | None


_METRIC_FIELDS = {f.name for f in fields(FactorInputs)} - {"symbol"}


def _zscores(values: list[float | None]) -> list[float | None]:
    """Cross-sectional z-scores, ignoring ``None``. Needs >=2 present values.

    Uses population std; when the spread is zero (or <2 present) every present
    value maps to 0.0 (no dispersion -> no cross-sectional information).
    """
    present = np.array([v for v in values if v is not None], dtype=float)
    if present.size < 2:
        return [None if v is None else 0.0 for v in values]
    mu = float(present.mean())
    sd = float(present.std())  # population (ddof=0)
    if sd == 0.0:
        return [None if v is None else 0.0 for v in values]
    return [None if v is None else (float(v) - mu) / sd for v in values]


def _factor_zmatrix(inputs: Sequence[FactorInputs]) -> dict[str, list[float | None]]:
    """Per-metric sign-adjusted cross-sectional z-scores across the universe."""
    zmat: dict[str, list[float | None]] = {}
    for metrics in FACTOR_DEFS.values():
        for metric, direction in metrics:
            col = [getattr(inp, metric) for inp in inputs]
            zmat[metric] = [None if z is None else direction * z for z in _zscores(col)]
    return zmat


def _mean_ignore_none(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return sum(present) / len(present) if present else None


def compute_factor_scores(
    inputs: Sequence[FactorInputs], *, weights: Mapping[str, float] | None = None
) -> list[FactorScores]:
    """Cross-sectional factor + composite scores for every name in ``inputs``.

    Each factor score is the mean of its available sign-adjusted metric z-scores;
    the composite is the weight-normalized mean of the available factor scores
    (so a name missing a whole factor is still scored on the rest).
    """
    if not inputs:
        return []
    w = dict(weights) if weights is not None else DEFAULT_WEIGHTS
    zmat = _factor_zmatrix(inputs)

    out: list[FactorScores] = []
    for i, inp in enumerate(inputs):
        factor_score: dict[str, float | None] = {}
        for factor, metrics in FACTOR_DEFS.items():
            factor_score[factor] = _mean_ignore_none([zmat[m][i] for m, _ in metrics])

        num = 0.0
        den = 0.0
        for factor, score in factor_score.items():
            if score is not None and w.get(factor):
                num += w[factor] * score
                den += w[factor]
        composite = num / den if den else None

        out.append(
            FactorScores(
                symbol=inp.symbol,
                value=factor_score["value"],
                quality=factor_score["quality"],
                growth=factor_score["growth"],
                momentum=factor_score["momentum"],
                risk=factor_score["risk"],
                composite=composite,
            )
        )
    return out


def inputs_from_mapping(symbol: str, data: Mapping[str, object]) -> FactorInputs:
    """Build ``FactorInputs`` from a loose mapping, keeping only known metrics."""
    kw = {k: data[k] for k in _METRIC_FIELDS if isinstance(data.get(k), (int, float))}
    return FactorInputs(symbol=symbol, **kw)  # type: ignore[arg-type]
