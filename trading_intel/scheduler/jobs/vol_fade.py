"""Scheduled job (EOD, after the 16:15 VIX close): the vf-v2 spike-fade signal.

Thin wrapper around ``strategies.vol_fade``: reads the full Cboe close histories of VIX, VIX9D and
VVIX (free CDN, ``clients/cboe.py``), adds today's closes from the delayed quotes when the history
files do not carry today yet, banks today's ``VOL_FADE_STATE`` (+ ``VOL_FADE_FIRE`` on a fire day)
and pushes each NEW event to Telegram:
  ARMED         VIX closed at 25+ (a 20-session window opens)
  FIRE          fear release inside the window - buy SVXY at the next open (strong = VVIX 110+)
  FEAR_RETURNS  VIX9D back above VIX during a hold (warning only, untested)
  LAST_DAY      tomorrow's close is the 20th session of a trade
  EXIT          today's close was the 20th session; the result (SVXY entry open -> close) is added

Idempotent (rule 5): one state row per session; the row remembers which events were pushed, so a
re-run pushes nothing new. Non-sessions are skipped (a weekend run would re-read Friday's quotes).

DSM (NAS): Mon-Fri 17:13 ET -
  docker run --rm --env-file .env trading-intel python -m trading_intel.scheduler.jobs.vol_fade

Manual run:
    python -m trading_intel.scheduler.jobs.vol_fade            # bank + push
    python -m trading_intel.scheduler.jobs.vol_fade --dry-run  # print today's state, write nothing
"""

from __future__ import annotations

import sys
import uuid
from datetime import date
from typing import TYPE_CHECKING, Any

import structlog

from trading_intel.config import Settings, get_settings
from trading_intel.strategies import vol_fade as strategy
from trading_intel.timeutils import eastern_now, is_trading_session

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = structlog.get_logger(__name__)

_ICON = {"ARMED": "🔔", "FIRE": "🚨", "FEAR_RETURNS": "⚠️", "LAST_DAY": "⏰", "EXIT": "✅"}


def load_rows(cboe: Any, today: date) -> list[strategy.Row]:
    """Cboe close histories joined on date; today's closes from the quotes fill whatever the
    history files do not have yet (they post some time after the close)."""
    rows = strategy.merge_closes(
        cboe.index_history("VIX"), cboe.index_history("VIX9D"), cboe.index_history("VVIX")
    )
    rows = [r for r in rows if r[0] <= today]
    if not is_trading_session(today):
        return rows  # never file quotes under a closed day
    have = rows[-1] if rows and rows[-1][0] == today else None
    if have and have[2] and have[3]:
        return rows
    term = cboe.term_structure() or {}
    q_vix, q_v9, q_vv = term.get("VIX"), term.get("VIX9D"), cboe.vvix()
    if have:
        rows[-1] = (today, have[1], have[2] or q_v9, have[3] or q_vv)
    elif q_vix:
        rows.append((today, float(q_vix), q_v9, q_vv))
    return rows


def _history_line(strength: str | None) -> str:
    rows = strategy.EVIDENCE["rows"]
    key = "strong_20" if strength == "strong" else "normal_20"
    svxy = next(r for r in rows[key] if r[0] == "long SVXY")
    allv = next(r for r in rows["all_20"] if r[0] == "short VIXY")
    return (
        f"History, 20 sessions: {strength or 'these'} fires, long SVXY {svxy[2]} trades "
        f"{svxy[3]:+.1f}% avg, {svxy[4]}% up, worst {svxy[5]:.0f}%. "
        f"All fires since 2011 (short VIXY): {allv[2]} trades {allv[3]:+.1f}%, {allv[4]}% up."
    )


def trade_result(prices: Any, fire_day: str, today: date) -> str | None:
    """SVXY from the open after the fire to today's close, or None when prices are missing."""
    try:
        df = prices.daily_history("SVXY", period="3mo")
        df = df[df["date"].dt.date > date.fromisoformat(fire_day)]
        if df.empty:
            return None
        entry = float(df.iloc[0]["open"] or df.iloc[0]["close"])
        last = df[df["date"].dt.date <= today]
        if last.empty or not entry:
            return None
        ret = float(last.iloc[-1]["close"]) / entry - 1.0
        return f"SVXY {entry:.2f} -> {float(last.iloc[-1]['close']):.2f} = {ret * 100:+.1f}%"
    except Exception as exc:  # a score line is optional; never fail the job
        log.warning("vol_fade.result_failed", error=str(exc)[:120])
        return None


def format_event(ev: str, st: strategy.DayState, result: str | None = None) -> str:
    head = f"{_ICON.get(ev, '')} <b>Vol fade · {ev.replace('_', ' ')}</b>"
    if ev == "FIRE" and st.fire:
        f = st.fire
        body = (
            f"Fear released after the VIX spike: VIX {f['vix']}, VIX9D/VIX {st.v9r}, "
            f"VVIX {f['vvix']} = <b>{f['strength']}</b>.\nBuy SVXY at the next open, "
            f"{strategy.UNITS} unit; sell at the close of the 20th session. SVIX swings about "
            f"twice as much. No naked UVXY short.\n" + _history_line(f["strength"])
        )
        return f"{head}\n{body}"
    text = strategy.headline(st)
    if ev == "EXIT" and result:
        text += f"\nResult: {result}."
    if ev == "FEAR_RETURNS":
        text = (
            f"VIX9D/VIX {st.v9r} is back above 1.00 during the hold. Untested warning: the rule "
            f"still holds to the 20th session.\n" + text
        )
    return f"{head}\n{text}"


def run(
    session: Session,
    *,
    settings: Settings | None = None,
    cboe: Any = None,
    prices: Any = None,
    today: date | None = None,
    dry_run: bool = False,
    tg: Any = None,
) -> list[str]:
    settings = settings or get_settings()
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="vol_fade")
    today = today or eastern_now().date()
    if not dry_run and not is_trading_session(today):
        bound.info("vol_fade.skipped_non_session", as_of=today.isoformat())
        return []
    if cboe is None:
        from trading_intel.clients.cboe import CboeClient

        cboe = CboeClient(timeout=30.0)
    rows = load_rows(cboe, today)
    if rows and dry_run:
        pass  # dry run: show the latest session we have
    elif not rows or rows[-1][0] != today:
        bound.warning(
            "vol_fade.no_close_today",
            as_of=today.isoformat(),
            last=rows[-1][0].isoformat() if rows else None,
        )
        return []
    states = strategy.evaluate(rows)
    st = states[-1]
    if dry_run:
        print(st.d, st.phase, "|", strategy.headline(st), "| events:", st.events)
        return st.events
    st, new = strategy.emit_signals(session, states)
    session.commit()
    if new and st is not None:
        if tg is None:
            from trading_intel.clients.telegram import TelegramClient

            tg = TelegramClient(settings)
        result = None
        if "EXIT" in new:
            if prices is None:
                from trading_intel.clients.fmp_prices import fmp_price_source

                prices = fmp_price_source(settings)
            done = [t for t in st.trades if t["left"] == 0]
            result = trade_result(prices, done[-1]["d"], today) if done else None
        for ev in new:
            tg.send_message(format_event(ev, st, result))
    bound.info("vol_fade.done", as_of=today.isoformat(), phase=st.phase if st else None, new=new)
    return new


if __name__ == "__main__":  # pragma: no cover
    from trading_intel.memory.db import make_session_factory

    s = get_settings()
    with make_session_factory(s)() as sess:
        run(sess, settings=s, dry_run="--dry-run" in sys.argv)
