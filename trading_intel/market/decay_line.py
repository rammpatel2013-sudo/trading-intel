"""Decay line: where time decay of THIS WEEK's expiring SPX options flips dealers from buying to selling.

VolSignals (Nov 16-20 2026) read the expiry week off one level: the strike where the dealer
"decay hedge" changes sign (7,500 that week - above it decay = dealers BUY futures, below it they
SELL; it held at 25 of 25 marks). Our week map's decay zones use the whole book with the naive
sign and showed "dealers buy" at every level (simulation 2026-11-21, project doc
claude/x7d-vs-alma-20261121.md). This module computes the line three ways so it can be scored in
the background before anything goes on the MAP:

    N  naive whole expiring book   dealers long every call, short every put
    W  naive near-money only       same, strikes within +-2.5% of the reference price
    L  signed ledger + near-money  pos_ledger (dealer = -customer net) where it has the contract,
                                   near-money naive for the AM-settled monthly series (not in the ledger)

Decay hedge at price S and mark time t = ES futures dealers trade if S is unchanged until each
contract settles: -sum(dealer_pos x (delta_at_settlement - delta_now)) x 2  (SPX 100x, ES 50x).
Positive = dealers BUY. Pure maths, no DB. Descriptor only (FlashAlpha rule 4).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

import numpy as np

#: near-money window for the naive AM series (W and L)
NEAR_PCT = 0.025
#: ES contracts per SPX option contract (100 / 50)
ES_PER_SPX = 2.0
VARIANTS = ("N", "W", "L")


@dataclass(frozen=True)
class Contract:
    strike: float
    is_call: bool
    oi: float
    iv: float
    settles: datetime          # naive Eastern settlement time (AM 09:30 / PM 16:00)
    ledger_pos: float | None   # customer net from pos_ledger; None = not in the ledger


def _ncdf(x: np.ndarray) -> np.ndarray:
    z = np.abs(x) / math.sqrt(2.0)
    t = 1.0 / (1.0 + 0.3275911 * z)
    y = 1.0 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t
               + 0.254829592) * t * np.exp(-z * z)
    return 0.5 * (1.0 + np.sign(x) * y)


def dealer_positions(contracts: Sequence[Contract], ref_price: float) -> dict[str, np.ndarray]:
    """Dealer position per contract under each variant (positive = dealer long)."""
    oi = np.array([c.oi for c in contracts], dtype=float)
    calls = np.array([c.is_call for c in contracts])
    naive = np.where(calls, oi, -oi)
    near = np.array([abs(c.strike / ref_price - 1.0) <= NEAR_PCT for c in contracts])
    led = np.array([np.nan if c.ledger_pos is None else -c.ledger_pos for c in contracts], dtype=float)
    signed = np.where(np.isnan(led), naive * near, led)
    return {"N": naive, "W": naive * near, "L": signed}


def decay_curve(contracts: Sequence[Contract], pos: np.ndarray, grid: np.ndarray, now: datetime) -> np.ndarray:
    """ES dealers trade from decay alone, at each grid price (positive = buy)."""
    if not contracts:
        return np.zeros_like(grid, dtype=float)
    k = np.array([c.strike for c in contracts], dtype=float)
    iv = np.array([c.iv for c in contracts], dtype=float)
    calls = np.array([c.is_call for c in contracts])
    t = np.array([max((c.settles - now).total_seconds(), 0.0) / 86400.0 / 365.0 for c in contracts])
    alive = t > 0
    s = grid[:, None]
    sq = np.sqrt(np.maximum(t, 1e-9))[None, :]
    d1 = (np.log(s / k) + 0.5 * iv ** 2 * t) / (iv * sq)
    nd = _ncdf(d1)
    delta_now = np.where(calls, nd, nd - 1.0)
    delta_end = np.where(calls, (s > k).astype(float), -(s < k).astype(float))
    return -((delta_end - delta_now) * (pos * alive)).sum(axis=1) * ES_PER_SPX


def flips(grid: Sequence[float], curve: Sequence[float]) -> list[float]:
    """Prices where the curve changes sign (linear interpolation)."""
    out: list[float] = []
    for i in range(len(grid) - 1):
        a, b = curve[i], curve[i + 1]
        if a * b < 0:
            out.append(round(grid[i] + (grid[i + 1] - grid[i]) * a / (a - b), 1))
    return out


def value_at(grid: Sequence[float], curve: Sequence[float], price: float) -> float:
    return float(np.interp(price, grid, curve))


def nearest(levels: Iterable[float], price: float, n: int = 4) -> list[float]:
    return sorted(sorted(levels, key=lambda x: abs(x - price))[:n])
