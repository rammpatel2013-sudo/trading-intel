"""Beta-reset monitor (Phase 5 B1, 2026-11-04) — pure, no I/O.

Mithil's rule: timing low beta is hard, so wait for the moment low beta finally
breaks (correlations -> 1) and check that high beta holds up vs the market AND vs
its own beta. That washout is the reset. Spec: project doc
``claude/beta-reset-build-plan.md``.

Q1 "has low beta broken?" — three checks, each on SPLV (S&P low volatility):
  absolute    SPLV >= 3% off its 20-day high while SPX is down on the week
  relative    SPLV/SPX made a 60-day high within the last 20 sessions and now
              closes below its 20-day average (the hedge stopped working)
  correlation SPLV's 20-day beta to SPX >= its 1-year median + 1 sd, OR the
              COR3M 5-day change is in the top decile of the last year
  state = broken (3/3) · breaking (2/3) · intact
Q2 "did high beta hold?" — SPHB (S&P high beta) residual vs SPX using a beta
  FROZEN the day before the episode started (a live beta absorbs the very
  outperformance being measured). Cumulative residual >= 0 -> holding.
Context — Cboe correlation skew: COR10D (upside wing) / COR90D (downside wing).
Q3 (EPS revision breadth by cohort) needs SPHB/SPLV constituents — not built yet.

Descriptor only (rule 4): states, never signals.
"""

from __future__ import annotations

import math
import statistics as stx
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date

ABS_OFF_HIGH = -0.03
HIGH_WIN = 20
REL_HIGH_WIN = 60
REL_MA = 20
BETA_WIN = 20
FROZEN_BETA_WIN = 60
YEAR = 252
COR_CHG_DAYS = 5
COR_TOP = 0.90
IDLE_RESID_WIN = 20  # no episode: residual over the last 20 sessions, beta frozen before it


@dataclass(slots=True)
class BetaResetRow:
    date: date
    spx: float
    splv: float
    sphb: float
    cor3m: float | None = None
    cor10d: float | None = None
    cor90d: float | None = None
    cor_skew_ratio: float | None = None
    splv_off_high: float | None = None
    spx_wk: float | None = None
    abs_break: bool = False
    rel_break: bool = False
    splv_beta20: float | None = None
    beta_hi: bool = False
    cor_jump: bool = False
    corr_break: bool = False
    low_beta_state: str = "intact"
    episode_start: date | None = None
    beta_frozen: float | None = None
    sphb_resid: float | None = None
    high_beta_state: str = "n/a"
    label: str = "no reset"

    def as_dict(self) -> dict:
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, date):
                d[k] = v.isoformat()
            elif isinstance(v, float):
                d[k] = round(v, 5)
        return d


def _rets(px: Sequence[float]) -> list[float]:
    return [math.log(px[i] / px[i - 1]) for i in range(1, len(px))]


def _beta(y: Sequence[float], x: Sequence[float]) -> float | None:
    n = len(x)
    if n < 5:
        return None
    mx, my = sum(x) / n, sum(y) / n
    vx = sum((a - mx) ** 2 for a in x)
    if vx <= 1e-14:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / vx


def compute(
    spx: dict[date, float],
    splv: dict[date, float],
    sphb: dict[date, float],
    cor: dict[str, dict[date, float]] | None = None,
) -> list[BetaResetRow]:
    """Rows on the common SPX/SPLV/SPHB calendar, ascending. ``cor`` keys: COR3M/COR10D/COR90D."""
    cor = cor or {}
    days = sorted(
        d for d in set(spx) & set(splv) & set(sphb) if spx[d] > 0 and splv[d] > 0 and sphb[d] > 0
    )
    P = [spx[d] for d in days]
    L = [splv[d] for d in days]
    H = [sphb[d] for d in days]
    rP, rL, rH = [0.0] + _rets(P), [0.0] + _rets(L), [0.0] + _rets(H)
    ratio = [l / p for l, p in zip(L, P)]
    c3 = cor.get("COR3M", {})
    c3_chg_hist: list[float] = []
    beta_hist: list[float] = []
    rows: list[BetaResetRow] = []
    episode: int | None = None
    for i, d in enumerate(days):
        r = BetaResetRow(date=d, spx=P[i], splv=L[i], sphb=H[i])
        r.cor3m = c3.get(d)
        r.cor10d = cor.get("COR10D", {}).get(d)
        r.cor90d = cor.get("COR90D", {}).get(d)
        if r.cor10d and r.cor90d:
            r.cor_skew_ratio = r.cor10d / r.cor90d
        if i >= HIGH_WIN:
            r.splv_off_high = L[i] / max(L[i - HIGH_WIN + 1 : i + 1]) - 1.0
        if i >= 5:
            r.spx_wk = P[i] / P[i - 5] - 1.0
        r.abs_break = bool(
            r.splv_off_high is not None and r.splv_off_high <= ABS_OFF_HIGH and (r.spx_wk or 0) < 0
        )
        if i >= REL_HIGH_WIN + REL_MA:
            ma = sum(ratio[i - REL_MA + 1 : i + 1]) / REL_MA
            recent_hi = any(
                ratio[j] >= max(ratio[j - REL_HIGH_WIN + 1 : j + 1])
                for j in range(i - REL_MA + 1, i + 1)
            )
            r.rel_break = recent_hi and ratio[i] < ma
        if i >= BETA_WIN:
            r.splv_beta20 = _beta(rL[i - BETA_WIN + 1 : i + 1], rP[i - BETA_WIN + 1 : i + 1])
            if r.splv_beta20 is not None:
                hist = beta_hist[-YEAR:]
                if len(hist) >= 60:
                    r.beta_hi = r.splv_beta20 >= stx.median(hist) + stx.pstdev(hist)
                beta_hist.append(r.splv_beta20)
        # COR3M 5-day change vs the last year of 5-day changes
        prev = days[i - COR_CHG_DAYS] if i >= COR_CHG_DAYS else None
        if prev is not None and r.cor3m is not None and c3.get(prev) is not None:
            chg = r.cor3m - c3[prev]
            if len(c3_chg_hist) >= 60:
                h = sorted(c3_chg_hist[-YEAR:])
                r.cor_jump = chg >= h[int(COR_TOP * (len(h) - 1))]
            c3_chg_hist.append(chg)
        r.corr_break = r.beta_hi or r.cor_jump
        n = int(r.abs_break) + int(r.rel_break) + int(r.corr_break)
        r.low_beta_state = "broken" if n == 3 else "breaking" if n == 2 else "intact"

        # episode bookkeeping: opens on the first non-intact day, closes after 10 intact days
        if r.low_beta_state != "intact":
            if episode is None:
                episode = i
        elif (
            episode is not None
            and all(x.low_beta_state == "intact" for x in rows[-9:])
            and len(rows) >= 9
        ):
            episode = None
        start = episode if episode is not None else max(1, i - IDLE_RESID_WIN + 1)
        r.episode_start = days[episode] if episode is not None else None
        b0, b1 = start - FROZEN_BETA_WIN, start  # beta window ends the day BEFORE the start
        if b0 >= 1:
            r.beta_frozen = _beta(rH[b0:b1], rP[b0:b1])
        if r.beta_frozen is not None:
            r.sphb_resid = sum(rH[j] - r.beta_frozen * rP[j] for j in range(start, i + 1))
            r.high_beta_state = "holding" if r.sphb_resid >= 0 else "lagging"
        if r.low_beta_state == "broken" and r.high_beta_state == "holding":
            r.label = "RESET"
        elif r.low_beta_state != "intact" and r.high_beta_state == "holding":
            r.label = "reset forming"
        elif r.low_beta_state != "intact":
            r.label = "low beta breaking, high beta not holding"
        rows.append(r)
    return rows


def plain_read(r: BetaResetRow | None) -> str:
    if r is None:
        return "No SPLV/SPHB history yet."
    parts = [
        f"Low beta (SPLV) is {r.low_beta_state}: "
        f"{'≥3% off its 20-day high in a down week' if r.abs_break else 'not falling with the market'}; "
        f"{'the low-beta hedge stopped working vs SPX' if r.rel_break else 'still holding vs SPX'}; "
        f"{'correlation is spiking' if r.corr_break else 'correlation normal'}."
    ]
    if r.sphb_resid is not None:
        where = (
            f"since {r.episode_start.isoformat()}"
            if r.episode_start
            else "over the last 20 sessions"
        )
        parts.append(
            f"High beta (SPHB) is {r.high_beta_state} vs its own beta ({r.beta_frozen:.2f}, frozen before the window): "
            f"{r.sphb_resid * 100:+.2f}% residual {where}."
        )
    if r.cor10d and r.cor90d:
        parts.append(
            f"Correlation skew COR10D/COR90D = {r.cor10d:.1f}/{r.cor90d:.1f} — the downside wing is what spikes when "
            f"correlations go to 1."
        )
    parts.append(f"Overall: {r.label.upper() if r.label == 'RESET' else r.label}.")
    return " ".join(parts)
