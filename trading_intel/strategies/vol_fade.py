"""Spike-fade rule for VIX - rule vf-v2, set 2026-11-14 (Mithil's yes 12:39 ET).

After a VIX spike, near-term fear (VIX9D) runs above 30-day fear (VIX). When it falls back the
spike is usually spent and volatility drifts lower for weeks; short-vol funds gain while it does.

Rule vf-v2 (daily Cboe closes of VIX, VIX9D and VVIX):
  ARMED  VIX closed at 25 or more within the last 20 sessions (that close = session 0).
  FIRE   a fear release while armed: VIX9D / VIX closed above 1.00 for at least 3 sessions in a
         row, then closes at or below 1.00. Every release inside the window fires (not only the
         first one).
  STRONG VVIX at 110 or more on the fire day (high vol-of-vol = the most likely path for VIX is
         down, the 11/05 UX1 implied-distribution post). "normal" below 110.
  TRADE  buy SVXY (-0.5x VIX futures) at the next session's open, 1 unit for now; SVIX (-1x)
         swings about twice as much. No naked short of UVXY / UVIX (a short can lose > 100%).
  EXIT   sell at the close of the 20th session held. Each fire is its own trade with its own clock.
  WARN   VIX9D / VIX back above 1.00 during a hold = "fear returns". A warning only: untested.

History (Cboe closes + FMP fund prices, research/vol_etp_backtest_20261114/bt_vf2.py; project doc
claude/vol-etp-backtest-20261114.md): see EVIDENCE. vf-v1 (first release after a FIRST close at
25+, size by the spike's peak) was frozen first and tested weaker (SVXY 12 trades, +1.7%, 67% up);
the 20-session window and the VVIX 110 cut were chosen AFTER looking at the results, so every live
fire is scored at 20 and 60 sessions before any size change (ledger section I).

The first half is pure (no IO; ``scripts/cvxmap/volx.py`` uses it for the cockpit card).
``emit_signals`` writes the ``signals`` table (rule 4: only strategies/ write there); the EOD job
is ``scheduler/jobs/vol_fade.py``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

RULE_VERSION = "vf-v2 (2026-11-14)"
SETUP_LEVEL = 25.0
ARM_WINDOW = 20
RUN_MIN = 3
HOLD = 20
STRONG_VVIX = 110.0
UNITS = 1

SYMBOL = "VIX"
SIGNAL_TYPE_STATE = "VOL_FADE_STATE"
SIGNAL_TYPE_FIRE = "VOL_FADE_FIRE"

# Frozen 2026-11-14 from bt_vf2.py (entry next open; shorts pay 5%/yr borrow). Each row:
# [trade, data since, n, avg %, up %, worst %]. Keys: all / strong / normal fires, 20-session hold;
# all_60 = the same fires held 60 sessions.
EVIDENCE: dict[str, Any] = {
    "rule": RULE_VERSION,
    "asof": "2026-11-14",
    "fires_since_2011": 49,
    "cols": ["trade", "since", "n", "avg %", "up %", "worst %"],
    "rows": {
        "all_20": [
            ["long SVXY", "2018", 28, 4.2, 79, -25.1],
            ["long SVIX", "2022", 16, 8.2, 81, -48.8],
            ["short VIXY", "2011", 49, 8.1, 82, -52.0],
            ["long SPY", "2011", 49, 2.5, 78, -9.1],
            ["short UVXY", "2018", 28, 13.3, 86, -73.9],
        ],
        "strong_20": [
            ["long SVXY", "2018", 16, 5.3, 81, -14.5],
            ["long SVIX", "2022", 6, 12.5, 83, -3.7],
            ["short VIXY", "2011", 21, 12.2, 95, -27.2],
            ["long SPY", "2011", 21, 3.3, 81, -3.9],
            ["short UVXY", "2018", 16, 17.9, 94, -37.9],
        ],
        "normal_20": [
            ["long SVXY", "2018", 12, 2.7, 75, -25.1],
            ["long SVIX", "2022", 10, 5.6, 80, -48.8],
            ["short VIXY", "2011", 28, 5.0, 71, -52.0],
            ["long SPY", "2011", 28, 2.0, 75, -9.1],
            ["short UVXY", "2018", 12, 7.1, 75, -73.9],
        ],
        "all_60": [
            ["long SVXY", "2018", 28, 9.0, 71, -15.3],
            ["long SVIX", "2022", 16, 24.9, 81, -26.6],
            ["short VIXY", "2011", 49, 20.9, 84, -24.5],
            ["long SPY", "2011", 49, 5.8, 82, -7.8],
            ["short UVXY", "2018", 28, 29.8, 86, -23.2],
        ],
    },
    "note": (
        "Strong = VVIX 110+ on the fire day; it mattered for the 20-session hold, by 60 sessions "
        "strong and normal did about the same. The 20-session window and the VVIX cut were chosen "
        "after "
        "looking, so each live fire is scored."
    ),
}

Row = tuple[date, float, float | None, float | None]  # (session, VIX, VIX9D, VVIX)


@dataclass
class DayState:
    d: date
    vix: float
    v9r: float | None
    vvix: float | None
    phase: str = "IDLE"  # IDLE | ARMED | FIRE | HOLD
    run_above1: int = 0  # VIX9D/VIX closes above 1.00 in a row up to today
    armed: dict | None = None  # {"last25_d", "last25_vix", "ago", "days_left"}
    fire: dict | None = None  # today's fire {"d", "vix", "vvix", "strength"}
    trades: list[dict] = field(default_factory=list)  # open holds, newest last
    fear_returns: bool = False
    events: list[str] = field(default_factory=list)  # ARMED / FIRE / FEAR_RETURNS / LAST_DAY / EXIT

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["d"] = self.d.isoformat()
        return out


def strength(vvix: float | None) -> str:
    if vvix is None or vvix <= 0:
        return "unknown"
    return "strong" if vvix >= STRONG_VVIX else "normal"


def evaluate(rows: list[Row]) -> list[DayState]:
    """Per-session states, ``rows`` ascending. No look-ahead: each day uses closes up to that day.
    A missing VIX9D resets the run (same counting as the 11/14 backtest)."""
    out: list[DayState] = []
    last_hit: int | None = None
    fires: list[tuple[int, dict]] = []
    run = 0
    for i, (d, vix, v9, vv) in enumerate(rows):
        r = (v9 / vix) if (v9 and vix and vix > 0) else None
        st = DayState(
            d=d,
            vix=round(vix, 2),
            v9r=round(r, 3) if r is not None else None,
            vvix=round(vv, 2) if vv else None,
        )
        was_armed = last_hit is not None and i - 1 - last_hit <= ARM_WINDOW
        if vix >= SETUP_LEVEL:
            last_hit = i
        armed = last_hit is not None and i - last_hit <= ARM_WINDOW
        if armed and not was_armed:
            st.events.append("ARMED")
        release = False
        if r is None:
            run = 0
        elif r > 1.0:
            run += 1
        else:
            release = run >= RUN_MIN
            run = 0
        st.run_above1 = run
        if armed:
            st.armed = {
                "last25_d": rows[last_hit][0].isoformat(),
                "last25_vix": round(rows[last_hit][1], 2),
                "ago": i - last_hit,
                "days_left": ARM_WINDOW - (i - last_hit),
            }
            st.phase = "ARMED"
        open_trades = [(fi, f) for fi, f in fires if 0 < i - fi <= HOLD]
        if open_trades:
            st.phase = "HOLD"
            st.trades = [f | {"hold_day": i - fi, "left": HOLD - (i - fi)} for fi, f in open_trades]
            st.fear_returns = r is not None and r > 1.0
            prev = out[-1] if out else None
            if st.fear_returns and not (prev and prev.trades and prev.fear_returns):
                st.events.append("FEAR_RETURNS")
            if any(t["hold_day"] == HOLD - 1 for t in st.trades):
                st.events.append("LAST_DAY")
            if any(t["hold_day"] == HOLD for t in st.trades):
                st.events.append("EXIT")
        if release and armed:
            f = {
                "d": d.isoformat(),
                "vix": round(vix, 2),
                "vvix": round(vv, 2) if vv else None,
                "strength": strength(vv),
                "units": UNITS,
            }
            fires.append((i, f))
            st.fire = f
            st.phase = "FIRE"
            st.events.append("FIRE")
        out.append(st)
    return out


def fire_days(states: list[DayState]) -> list[DayState]:
    return [s for s in states if "FIRE" in s.events]


def merge_closes(
    vix: list[tuple[date, float]],
    v9: list[tuple[date, float]],
    vvix: list[tuple[date, float]] | None = None,
    today: Row | None = None,
) -> list[Row]:
    """Join the Cboe close histories on date. ``today`` = the session's own closes from the delayed
    quotes, used only when the history file does not have that session yet (never replaces it)."""
    m9, mv = dict(v9), dict(vvix or [])
    rows: list[Row] = [(d, c, m9.get(d), mv.get(d)) for d, c in vix if c and c > 0]
    if today and today[1] and (not rows or rows[-1][0] < today[0]):
        rows.append(today)
    return rows


def recent_fires(states: list[DayState], n: int = 5) -> list[dict]:
    return [s.fire for s in fire_days(states)[-n:] if s.fire]


# ---------------------------------------------------------------- plain words (card / Telegram)
def _u(n: int) -> str:
    return f"{n} unit" + ("s" if n != 1 else "")


def headline(st: DayState) -> str:
    if st.phase == "FIRE":
        f = st.fire or {}
        tag = {"strong": "STRONG (VVIX 110+)", "normal": "normal (VVIX under 110)"}.get(
            f.get("strength"), "VVIX unknown"
        )
        return (
            f"FIRE - fear released after the VIX spike, {tag}. Buy SVXY at the next open, "
            f"{_u(UNITS)}; sell at the close of the 20th session."
        )
    if st.phase == "HOLD":
        t = st.trades[-1]
        more = f" ({len(st.trades)} open trades)" if len(st.trades) > 1 else ""
        warn = (
            " Fear returns: VIX9D is above VIX again (warning only, untested)."
            if st.fear_returns
            else ""
        )
        tail = f"{more}{warn}"
        if t["left"] == 0:
            return (
                f"EXIT - 20th session: the SVXY trade from {t['d']} is sold at today's close.{tail}"
            )
        if t["left"] == 1:
            return (
                f"LAST DAY TOMORROW - sell the SVXY trade from {t['d']} at tomorrow's close.{tail}"
            )
        day = f"day {t['hold_day']} of {HOLD}, {t['left']} left"
        return f"HOLD - SVXY trade from {t['d']}: {day}.{tail}"
    if st.phase == "ARMED":
        a = st.armed or {}
        return (
            f"ARMED - VIX closed at {a.get('last25_vix')} on {a.get('last25_d')}. Fires when VIX9D "
            f"drops back under VIX after 3+ closes above ({st.run_above1} so far); "
            f"{a.get('days_left')} sessions left."
        )
    return "IDLE - no VIX close at 25+ in the last 20 sessions: nothing to fade."


# ---------------------------------------------------------------- DB (signals table)
def _ts(d: date) -> datetime:
    return datetime.combine(d, datetime.min.time())


def emit_signals(session: Session, states: list[DayState]) -> tuple[DayState | None, list[str]]:
    """Write today's VOL_FADE_STATE (one row per session, updated in place on a re-run) and, on a
    fire day, one VOL_FADE_FIRE row. Returns (today's state, events not pushed before). The stored
    row remembers which events were already returned, so a re-run returns none (rule 5)."""
    import structlog
    from sqlalchemy import select

    from trading_intel.memory.models import Signal

    if not states:
        return None, []
    st = states[-1]
    ts = _ts(st.d)
    row = (
        session.execute(
            select(Signal)
            .where(
                Signal.signal_type == SIGNAL_TYPE_STATE, Signal.symbol == SYMBOL, Signal.ts == ts
            )
            .limit(1)
        )
        .scalars()
        .first()
    )
    done = set((row.payload or {}).get("pushed", [])) if row is not None else set()
    new = [e for e in st.events if e not in done]
    payload = st.to_dict() | {
        "rule": RULE_VERSION,
        "headline": headline(st),
        "evidence": EVIDENCE,
        "pushed": sorted(done | set(st.events)),
    }
    if row is None:
        session.add(
            Signal(
                ts=ts,
                symbol=SYMBOL,
                signal_type=SIGNAL_TYPE_STATE,
                payload=payload,
                confidence=None,
            )
        )
    else:
        row.payload = payload
    if "FIRE" in st.events:
        has = session.execute(
            select(Signal.id)
            .where(Signal.signal_type == SIGNAL_TYPE_FIRE, Signal.symbol == SYMBOL, Signal.ts == ts)
            .limit(1)
        ).scalar_one_or_none()
        if has is None:
            session.add(
                Signal(
                    ts=ts,
                    symbol=SYMBOL,
                    signal_type=SIGNAL_TYPE_FIRE,
                    payload=payload,
                    confidence=None,
                )
            )
    session.flush()
    structlog.get_logger(__name__).info(
        "vol_fade.emitted", as_of=st.d.isoformat(), phase=st.phase, new=new
    )
    return st, new
