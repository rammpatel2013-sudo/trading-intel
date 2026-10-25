"""The brief's MAP block: one place that answers "where are we on the map today".

Design rules for this module:

* **Reuse, don't recompute.** The flips already exist in the brief context and
  come from the house helpers. Recomputing them here would risk the brief
  contradicting the cockpit in the same breath.
* **Anchor the gamma to TODAY's spot.** The floor and cap are the heaviest
  gamma x OI strike each side — but gamma is recomputed from each contract's
  stored IV at this morning's spot, not read from the banked ``gxoi``, which
  froze it at the snapshot's spot and goes stale across an overnight gap.
* **Plain English on the page.** No Greek letters, no vendor field names. The
  numbers are the same; the reader shouldn't need the vocabulary. Anyone who
  wants the workings asks the MCP.

Both wall views are shown on purpose: the 0–7 DTE book is stable overnight,
while the same-day book is genuinely thin before 09:30 because most 0DTE open
interest is written intraday.

The signed-flow read is the one thing here with a REAL side — Convex's own
aggressor classification — rather than our calls-plus/puts-minus assumption.
It is one session's activity, not a standing position, and it is null for
every session banked before the 2026-10-25 collector widening.

Zero vendor calls — everything comes off the banked ``oi_chain_eod`` chain.
Descriptor only (FlashAlpha rule 4). ``build_map_block`` never raises; on any
failure it returns ``None`` and the brief renders without the block.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_intel.market import map_block as mbx
from trading_intel.memory.models import OiChainEod
from trading_intel.timeutils import eastern_now

log = structlog.get_logger(__name__)

_NEAR_DTE = 7
_CLOSE_HOUR = 16  # 16:00 ET
_YEAR_HOURS = 365.0 * 24.0
_SQRT_252 = 252.0 ** 0.5
# Build plan §6: a level is SOFT when a 1-vol parallel IV shift moves it by
# at least max(10 points, a quarter of the day's 1σ). The floor keeps a quiet
# tape from calling everything firm; the 1σ leg scales it on busy days.
_FRAGILE_FLOOR_PTS = 10.0
_FRAGILE_SIGMA_FRAC = 0.25
# Review fix #1: the badge reads "calibrating" until there is enough graded
# history to say whether soft days actually break more often than firm days.
# ~40 sessions ≈ the two months the plan sets for that review.
_CALIBRATION_SESSIONS = 40


def _hours_to_close(now: datetime | None = None) -> float:
    """Hours from now until today's 16:00 ET close (floored at ~6 minutes)."""
    now = now or eastern_now()
    close = now.replace(hour=_CLOSE_HOUR, minute=0, second=0, microsecond=0)
    if now >= close:
        close = close + timedelta(days=1)
    return max((close - now).total_seconds() / 3600.0, 0.1)


def _chain_rows(session: Session, symbol: str, today: date) -> tuple[list[tuple], datetime | None]:
    """The newest banked chain for ``symbol``, rows out to ``_NEAR_DTE`` days.

    DTE is recomputed against TODAY from the stored ``expiry``, not read from
    the stored ``dte`` column: if the newest pull is from a previous session
    its ``dte`` values are anchored to that day and would mislabel the books.

    The table is unique per (symbol, ts, SOURCE, expiry, strike, cp), so a ts
    carrying two vendors would double every gamma sum. Only the source with the
    most rows at that ts is kept.
    """
    ts = session.execute(
        select(func.max(OiChainEod.ts)).where(OiChainEod.symbol == symbol)
    ).scalar_one_or_none()
    if ts is None:
        return ([], None)
    horizon = today + timedelta(days=_NEAR_DTE)
    rows = session.execute(
        select(
            OiChainEod.strike,
            OiChainEod.cp,
            OiChainEod.expiry,
            OiChainEod.oi,
            OiChainEod.delta,
            OiChainEod.gamma,
            OiChainEod.iv,
            OiChainEod.gxoi,
            OiChainEod.gammas_buy,
            OiChainEod.gammas_sell,
            OiChainEod.source,
        ).where(
            OiChainEod.symbol == symbol,
            OiChainEod.ts == ts,
            OiChainEod.expiry >= today,
            OiChainEod.expiry <= horizon,
        )
    ).all()
    by_source: dict[str, list[tuple]] = {}
    for strike, cp, expiry, oi, delta, gamma, iv, gxoi, gbuy, gsell, source in rows:
        if expiry is None:
            continue
        dte = (expiry - today).days
        by_source.setdefault(str(source), []).append(
            (strike, cp, dte, oi, delta, gamma, iv, gxoi, gbuy, gsell)
        )
    if not by_source:
        return ([], ts)
    best = max(by_source, key=lambda k: len(by_source[k]))
    if len(by_source) > 1:
        log.info(
            "gamma_map.multi_source",
            ts=ts.isoformat() if ts else None,
            chose=best,
            sources={k: len(v) for k, v in by_source.items()},
        )
    return (by_source[best], ts)


def _fragility(
    near: mbx.Book,
    spot: float,
    *,
    atm_iv: float | None = None,
    iv_bump: float = 1.0,
    graded_sessions: int = 0,
) -> dict[str, Any]:
    """Do the levels survive a 1-vol IV wobble? (build plan §6)

    Walls and the flip are scored as SEPARATE legs (review fix #1): a book can
    easily have a pinned wall and a skittish flip, and averaging the two hides
    exactly the case worth knowing about.

    The threshold is ``max(10 pts, ¼ of the day's 1σ)``. Both legs use the
    Black-Scholes recompute (``walls`` / ``gamma_flip``) so the shifted and
    unshifted runs come from one model — the VENDOR walls are what the brief
    prints; this only answers whether they are worth leaning on.

    Until ``graded_sessions`` reaches the calibration bar the verdict is
    reported but flagged ``calibrating``: the threshold is a reasoned guess
    until soft days have been shown to break more often than firm ones.
    """
    sigma_day = spot * float(atm_iv) / _SQRT_252 if atm_iv else None
    tol = max(_FRAGILE_FLOOR_PTS, _FRAGILE_SIGMA_FRAC * sigma_day) if sigma_day else _FRAGILE_FLOOR_PTS

    base_pw, base_cw = mbx.walls(near, spot)
    base_flip = mbx.gamma_flip(near, spot)
    wall_moves: list[float] = []
    flip_moves: list[float] = []
    for shift in (iv_bump, -iv_bump):
        pw, cw = mbx.walls(near, spot, iv_shift=shift)
        flip = mbx.gamma_flip(near, spot, iv_shift=shift)
        for a, b in ((base_pw, pw), (base_cw, cw)):
            if a is not None and b is not None:
                wall_moves.append(abs(b - a))
        if base_flip is not None and flip is not None:
            flip_moves.append(abs(flip - base_flip))

    def leg(moves: list[float], name: str) -> dict[str, Any]:
        if not moves:
            return {"name": name, "firm": None, "move": None}
        worst = max(moves)
        return {"name": name, "firm": worst < tol, "move": worst}

    walls_leg = leg(wall_moves, "walls")
    flip_leg = leg(flip_moves, "flip")
    verdicts = [l["firm"] for l in (walls_leg, flip_leg) if l["firm"] is not None]
    firm = all(verdicts) if verdicts else None
    calibrating = graded_sessions < _CALIBRATION_SESSIONS

    if firm is None:
        label = "levels n/a"
    elif calibrating:
        label = "week levels firm (calibrating)" if firm else "week levels soft (calibrating)"
    else:
        label = "week levels firm" if firm else "week levels soft"

    soft_parts = [l["name"] for l in (walls_leg, flip_leg) if l["firm"] is False]
    return {
        "firm": firm,
        "label": label,
        "calibrating": calibrating,
        "graded_sessions": graded_sessions,
        "legs": [walls_leg, flip_leg],
        "soft_parts": soft_parts,
        "max_move": max([m for m in (walls_leg["move"], flip_leg["move"]) if m is not None], default=None),
        "tol": tol,
        "sigma_day": sigma_day,
        "iv_bump": iv_bump,
    }


def build_map_block(
    session: Session,
    ctx: dict[str, Any],
    *,
    symbol: str = "SPX",
    today: date | None = None,
    graded_sessions: int = 0,
) -> dict[str, Any] | None:
    """Assemble the MAP block, or ``None`` when there is no chain to read.

    ``graded_sessions`` is how many days of post-close grading exist; it only
    decides whether the fragility badge admits it is still calibrating.
    """
    today = today or date.today()
    sym = symbol.strip().upper()
    try:
        rows, ts = _chain_rows(session, sym, today)
        if not rows:
            return None

        doc = ctx.get("doc") or {}
        zero = ctx.get("zero_dte") or {}
        index = next(
            (ix for ix in (ctx.get("indices") or []) if ix.get("symbol") == sym), {}
        )
        spot = doc.get("spot") or index.get("spot") or zero.get("spot")
        if not spot:
            return None
        spot = float(spot)

        near = mbx.build_book(rows, dte_max=_NEAR_DTE, label="0-7 DTE")
        same = mbx.build_book(rows, dte_max=0, label="same day")
        if near.empty:
            return None

        # --- regime + the two switches (read, not recomputed) ---------------
        book_flip = index.get("flip")
        day_flip = zero.get("flip")
        net_gamma = mbx.dealer_gamma_dollars(near, spot)
        long_gamma = net_gamma > 0
        switches = [
            {"name": "whole-book flip", "value": book_flip,
             "above": (spot > book_flip) if book_flip else None},
            {"name": "same-day flip", "value": day_flip,
             "above": (spot > day_flip) if day_flip else None},
        ]
        known = [s["value"] for s in switches if s["value"]]
        between = (
            min(known) <= spot <= max(known) if len(known) == 2 and known[0] != known[1] else None
        )

        # --- floor & cap, both books ---------------------------------
        # Gamma is recomputed at THIS morning's spot (see map_block.walls);
        # the stored gxoi froze it at the snapshot's spot and goes stale on a gap.
        near_pw, near_cw = mbx.walls(near, spot)
        day_pw, day_cw = mbx.walls(same, spot) if not same.empty else (None, None)
        wall_views = [
            {
                "label": "This week",
                "note": "holds up overnight \u2014 the one to trust",
                "put_wall": near_pw,
                "call_wall": near_cw,
                "stale": bool(doc.get("walls_stale")),
            },
            {
                "label": "Today only",
                "note": "most of it gets written after 9:30",
                "put_wall": day_pw or zero.get("put_wall"),
                "call_wall": day_cw or zero.get("call_wall"),
                "stale": True,
            },
        ]
        heaviest = mbx.heaviest_strike(near, spot)

        # --- who actually traded (real side, one session) --------------
        flow = mbx.signed_flow(near, strikes=[near_pw, near_cw])

        # --- the rest ---------------------------------------------------
        iv_atm = mbx.atm_iv(same if not same.empty else near, spot)
        cones = mbx.delta_cones(rows, spot, expiry_dte=0 if not same.empty else 1)
        if all(c["upper"] is None and c["lower"] is None for c in cones):
            cones = mbx.delta_cones(rows, spot, expiry_dte=_front_dte(rows, today))

        hedge = mbx.hedge_pressure(near, spot)
        frag = _fragility(near, spot, atm_iv=iv_atm, graded_sessions=graded_sessions)

        age_days = (today - ts.date()).days if ts else None
        return {
            "symbol": sym,
            "spot": spot,
            "as_of": ts.isoformat() if ts else None,
            "chain_age_days": age_days,
            "chain_stale": bool(age_days and age_days > 1),
            "regime": {
                "long_gamma": long_gamma,
                "label": (
                    "dealers long gamma — moves damped"
                    if long_gamma
                    else "dealers short gamma — moves amplified"
                ),
                "net_gamma_dollars": net_gamma,
                "between_switches": between,
            },
            "switches": switches,
            "wall_views": wall_views,
            "heaviest": heaviest,
            "flow": flow,
            "atm_iv": iv_atm,
            "cones": cones,
            "hedge": hedge,
            "fragility": frag,
            "n_contracts": near.n,
            "caveat": (
                "Dealer book INFERRED from naive OI (calls +, puts −), not cleared "
                "positioning. Probabilities are what the options price in, not a "
                "forecast. Descriptor, not a signal."
            ),
        }
    except Exception as exc:  # noqa: BLE001 — the block is additive, never fatal
        log.warning("gamma_map.failed", error=str(exc), exc_info=True)
        return None


def _front_dte(rows: list[tuple], today: date) -> int:
    """Smallest DTE present in the rows (0 when the list is empty)."""
    dtes = [int(r[2]) for r in rows if r[2] is not None and int(r[2]) >= 0]
    return min(dtes) if dtes else 0
