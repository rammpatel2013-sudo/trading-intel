"""Norseman Market Timing regime monitor — state + state-change events.

Runs NMT's three weekend questions off OUR data every session (math in
``market.norseman``) and emits:

* ``NORSEMAN_STATE`` — one per session: the full read (line, clock, 10% closes, dials).
* ``NORSEMAN_EVENT`` — only when the method reaches a decision point:

  ``PROXIMITY``     SPX closes within 5% / 2% of the Bull/Bear Line
  ``TEST``          intraday low taps the line (≤1% above it) → clock resets
  ``VIOLATION``     WEEKLY close below the line (1st = test spent, 2nd = terminal)
  ``CLOCK_OPEN``    session 128 since the last test → a −10% is "permitted"
  ``TARGET``        SPX trades through an author target (7,930 / 8,125 ASSESS …)
  ``ASSESS_START``  new SPX high NOT confirmed by A-D line / RSP / IWM
  ``ASSESS_MATURE`` that divergence gap reaches 14 sessions
  ``LINE_MISMATCH`` our line ≠ the author's stated line (data check)
  ``WEEKLY_GRADE``  Friday close — the three questions graded (always sent)

Every event carries a dedupe ``key`` so re-runs never re-alert (rule 5). Only this
strategy writes NORSEMAN_* rows (rule 4). The method is the author's published,
graded-in-public system; our job is to detect when price/breadth reach his
decision points — it is a regime read, not an entry signal.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.market import norseman as nm
from trading_intel.memory.models import BreadthSnapshot, NewsletterLevel, QuoteDaily, Signal
from trading_intel.timeutils import is_trading_session

log = structlog.get_logger(__name__)

BULL_START = date(2025, 4, 8)  # author's cyclical buy (Muninn long since 4/8/25)
SYMBOL = "SPX"
SIGNAL_STATE = "NORSEMAN_STATE"
SIGNAL_EVENT = "NORSEMAN_EVENT"
PROX_BANDS = (0.02, 0.05)  # tightest first
MISMATCH_TOL = 0.0025
TARGET_NAMES = ("target", "assess_zone")
CORE_RAILS = ("A-D", "RSP")  # the rails his divergence call uses; IWM = early context


@dataclass(slots=True)
class NorsemanState:
    as_of: date
    spx_close: float
    spx_high: float
    spx_low: float
    ath: float
    ath_date: date
    line: float  # line in force AFTER today's session
    line_in_force: float  # line that applied TO today's session
    dist_to_line: float  # close / line − 1
    week_close: bool
    last_test: nm.TestEpisode | None
    session: int | None
    clock_open_date: date | None
    clock_open: bool
    count_start: date | None
    violations: list[tuple[date, float, float]]
    tapped_today: bool
    dials: nm.DialsRead
    author: dict[str, Any] = field(default_factory=dict)
    author_line_ours: float | None = None  # our line as of the author's letter date


# ── readers ──────────────────────────────────────────────────────────────

def _bars(session: Session, symbol: str, *, as_of: date) -> list[nm.Bar]:
    rows = session.execute(
        select(QuoteDaily.date, QuoteDaily.high, QuoteDaily.low, QuoteDaily.close)
        .where(QuoteDaily.symbol == symbol, QuoteDaily.date <= as_of)
        .order_by(QuoteDaily.date)
    ).all()
    return [nm.Bar(d, float(h or c), float(lo or c), float(c)) for d, h, lo, c in rows if c]


def spx_bars(session: Session, *, as_of: date) -> list[nm.Bar]:
    """Real SPX bars; if SPX lags SPY, extend with SPY scaled by the last SPX/SPY ratio."""
    spx = _bars(session, "SPX", as_of=as_of)
    spy = _bars(session, "SPY", as_of=as_of)
    if not spx:
        return [nm.Bar(b.d, b.high * 10, b.low * 10, b.close * 10) for b in spy]
    last = spx[-1].d
    tail = [b for b in spy if b.d > last]
    if tail:
        ref = next((b for b in reversed(spy) if b.d == last), None)
        k = spx[-1].close / ref.close if ref else 10.0
        spx += [nm.Bar(b.d, b.high * k, b.low * k, b.close * k) for b in tail]
        log.warning("norseman.spx_extended_with_spy", n=len(tail), ratio=round(k, 4))
    return spx


def _closes(session: Session, symbol: str, *, as_of: date) -> list[tuple[date, float]]:
    return [(b.d, b.close) for b in _bars(session, symbol, as_of=as_of)]


def _ad_line(session: Session, *, as_of: date) -> list[tuple[date, float]]:
    rows = session.execute(
        select(BreadthSnapshot.ts, BreadthSnapshot.ad_line)
        .where(BreadthSnapshot.ad_line.is_not(None), BreadthSnapshot.ts <= as_of)
        .order_by(BreadthSnapshot.ts)
    ).all()
    return [(d, float(v)) for d, v in rows]


def author_levels(session: Session) -> dict[str, Any]:
    """Latest NORSEMAN levels banked in newsletter_levels → {name: value, as_of}."""
    latest = session.execute(
        select(NewsletterLevel.as_of)
        .where(NewsletterLevel.source == "NORSEMAN")
        .order_by(NewsletterLevel.as_of.desc())
    ).scalars().first()
    if latest is None:
        return {}
    rows = session.execute(
        select(NewsletterLevel.name, NewsletterLevel.value)
        .where(NewsletterLevel.source == "NORSEMAN", NewsletterLevel.as_of == latest)
    ).all()
    out: dict[str, Any] = {n: v for n, v in rows if v is not None}
    out["as_of"] = latest
    return out


# ── the read ─────────────────────────────────────────────────────────────

def read_state(session: Session, *, as_of: date | None = None) -> NorsemanState | None:
    as_of = as_of or date.today()
    bars = spx_bars(session, as_of=as_of)
    if len(bars) < 30:
        return None
    today = bars[-1]
    dates = [b.d for b in bars]
    ath_bar = max(bars, key=lambda b: (b.high, b.d))
    line = 0.9 * ath_bar.high
    lines = {ld.d: ld.line for ld in nm.line_series(bars)}
    line_before = lines.get(today.d, line)

    episodes = nm.test_episodes(bars)
    last_test = episodes[-1] if episodes else None
    sess = nm.sessions_between(dates, last_test.anchor, today.d) if last_test else None
    clock_date = None
    if last_test and sess is not None:
        remaining = nm.CLOCK_OPEN_SESSION - sess
        clock_date = today.d if remaining <= 0 else nm.project_session_date(
            today.d, remaining, is_trading_session)
    start = nm.bull_count_start(bars, bull_start=BULL_START)
    viols = nm.violations(bars, since=start) if start else []
    nxt = nm.project_session_date(today.d, 1, is_trading_session)

    since = last_test.anchor if last_test else dates[0]
    rails = [
        nm.rail_read("A-D line (S&P 500)", _ad_line(session, as_of=as_of), since=since,
                     price_high_date=ath_bar.d, session_dates=dates),
        nm.rail_read("RSP (average stock)", _closes(session, "RSP", as_of=as_of), since=since,
                     price_high_date=ath_bar.d, session_dates=dates),
        nm.rail_read("IWM (small caps)", _closes(session, "IWM", as_of=as_of), since=since,
                     price_high_date=ath_bar.d, session_dates=dates),
    ]
    author = author_levels(session)
    ours_at_author = None
    if author.get("as_of"):
        prior = [b for b in bars if b.d <= author["as_of"]]
        if prior:
            ours_at_author = 0.9 * max(b.high for b in prior)

    return NorsemanState(
        as_of=today.d, spx_close=today.close, spx_high=today.high, spx_low=today.low,
        ath=ath_bar.high, ath_date=ath_bar.d, line=line, line_in_force=line_before,
        dist_to_line=today.close / line - 1.0,
        week_close=nxt.isocalendar()[:2] != today.d.isocalendar()[:2],
        last_test=last_test, session=sess, clock_open_date=clock_date,
        clock_open=bool(sess is not None and sess >= nm.CLOCK_OPEN_SESSION),
        count_start=start, violations=viols,
        tapped_today=today.low <= line_before * (1.0 + nm.TEST_BAND),
        dials=nm.DialsRead(price_high_date=ath_bar.d, rails=rails),
        author=author, author_line_ours=ours_at_author,
    )


# ── events (decision points) ────────────────────────────────────────────

def _f(x: float | None, nd: int = 2) -> str:
    return "n/a" if x is None else f"{x:,.{nd}f}"


def detect_events(st: NorsemanState) -> list[dict[str, Any]]:
    """Every decision point the method has reached as of ``st.as_of`` (keyed)."""
    ev: list[dict[str, Any]] = []
    for band in PROX_BANDS:
        if 0 <= st.dist_to_line <= band:
            ev.append({"kind": "PROXIMITY", "key": f"prox{int(band * 100)}:{st.line:.0f}",
                       "severity": "high" if band <= 0.02 else "medium",
                       "text": f"SPX {_f(st.spx_close)} is {st.dist_to_line:.1%} above the "
                               f"Bull/Bear Line {_f(st.line)} (inside the {band:.0%} band). "
                               "Test zone approaching — Q1 'is it time for a test?' now live."})
            break

    if st.tapped_today and st.last_test:
        ev.append({"kind": "TEST", "key": f"test:{st.last_test.start}", "severity": "high",
                   "text": f"B/BL TEST — SPX low {_f(st.spx_low)} tapped the line "
                           f"{_f(st.line)}. The clock resets to session 0; a bull can survive "
                           "one test. What matters now is the WEEKLY close vs the line."})

    if st.week_close and st.spx_close < st.line_in_force:
        n = len(st.violations) or 1
        terminal = n >= 2
        ev.append({"kind": "VIOLATION", "key": f"violation:{st.as_of}", "severity": "critical",
                   "text": f"10% WEEKLY CLOSE (#{n} this bull): SPX closed the week "
                           f"{_f(st.spx_close)} below the line {_f(st.line_in_force)}. "
                           + ("TERMINAL — no bull since 1940 has held two. Full bear-prepare."
                              if terminal else
                              "Test spent. With divergences = bear-prepare mode; "
                              "a second one is terminal.")})

    if st.clock_open and st.last_test:
        ev.append({"kind": "CLOCK_OPEN", "key": f"clock:{st.last_test.anchor}",
                   "severity": "medium",
                   "text": f"CLOCK OPEN — session {st.session} since the "
                           f"{st.last_test.anchor:%-m/%-d/%y} test. A −10% to the line "
                           f"({_f(st.line)}, {st.dist_to_line:.1%} away) is now permitted by "
                           "structure (not predicted). Watch the dials."})

    for name in TARGET_NAMES:
        lvl = st.author.get(name)
        if lvl and st.spx_high >= float(lvl):
            label = "ASSESS zone" if name == "assess_zone" else "target"
            ev.append({"kind": "TARGET", "key": f"target:{name}:{float(lvl):.2f}",
                       "severity": "medium",
                       "text": f"SPX traded {_f(st.spx_high)} through the author's {label} "
                               f"{_f(float(lvl))}. "
                               + ("Stop and grade: does breadth confirm this high?"
                                  if name == "assess_zone" else "Next: 8,125 ASSESS zone.")})

    div = [r for r in st.dials.diverging if r.name.split(" ")[0] in CORE_RAILS]
    if st.ath_date == st.as_of and div:
        first = max(div, key=lambda r: r.gap_sessions or 0)
        ev.append({"kind": "ASSESS_START", "key": f"assess:{first.name}:{first.peak_date}",
                   "severity": "high",
                   "text": "NEW SPX HIGH NOT CONFIRMED — "
                           + "; ".join(f"{r.name} peaked {r.peak_date:%-m/%-d} "
                                       f"({r.gap_sessions} sessions earlier)" for r in div)
                           + _small_caps_note(st)
                           + ". ASSESS begins: this is the 'top is a process' divergence. "
                             "Priors ran 21 (2018) / 53 (2024-25) sessions before price topped."})
    gap = max((r.gap_sessions or 0 for r in div), default=None)
    if div and gap is not None and gap >= nm.ASSESS_MATURE_GAP:
        r = max(div, key=lambda x: x.gap_sessions or 0)
        ev.append({"kind": "ASSESS_MATURE", "key": f"assess14:{r.name}:{r.peak_date}",
                   "severity": "high",
                   "text": f"Divergence gap {gap} sessions ({r.name} peak {r.peak_date:%-m/%-d})"
                           " — past the 14-session minimum warning. A top is now structurally "
                           "possible; the line is still the only exit."})

    a_line = st.author.get("bull_bear_line")
    if a_line and st.author_line_ours and abs(st.author_line_ours / a_line - 1) > MISMATCH_TOL:
        ev.append({"kind": "LINE_MISMATCH", "key": f"mismatch:{st.author['as_of']}",
                   "severity": "low",
                   "text": f"Data check: author's line {_f(a_line)} vs ours "
                           f"{_f(st.author_line_ours)} as of {st.author['as_of']}."})

    if st.week_close:
        ev.append({"kind": "WEEKLY_GRADE", "key": f"grade:{st.as_of}", "severity": "info",
                   "text": weekly_grade(st)})
    return ev


def _small_caps_note(st: NorsemanState) -> str:
    iwm = next((r for r in st.dials.rails if r.name.startswith("IWM") and r.diverging), None)
    return f"; small caps (IWM) topped first {iwm.peak_date:%-m/%-d}" if iwm else ""


def weekly_grade(st: NorsemanState) -> str:
    """The three weekend questions, graded on our data (Telegram HTML)."""
    q1 = ("OPEN" if st.clock_open else "NOT YET") + (
        f" — session {st.session}/{nm.CLOCK_OPEN_SESSION}"
        f"{'' if st.clock_open else f', opens {st.clock_open_date:%-m/%-d}'}"
        if st.session is not None else "")
    n = len(st.violations)
    q2 = ("NO" if st.spx_close >= st.line else "WEEKLY CLOSE BELOW") + (
        f" — 10% closes this bull: {n}")
    div = st.dials.diverging
    q3 = ("DIVERGING: " + ", ".join(f"{r.name} (gap {r.gap_sessions})" for r in div)
          if div else "NO DIVERGENCE — rails confirmed the last high")
    rails = "\n".join(
        f"  · {r.name}: peak {r.peak_date:%-m/%-d} {_f(r.peak)}, now {_f(r.last)}"
        for r in st.dials.rails if r.peak_date)
    tg = ", ".join(f"{k} {_f(float(st.author[k]))}" for k in TARGET_NAMES if st.author.get(k))
    return (f"<b>NMT weekly grade — {st.as_of:%a %-m/%-d}</b>\n"
            f"SPX {_f(st.spx_close)} · ATH {_f(st.ath)} ({st.ath_date:%-m/%-d})\n"
            f"B/BL {_f(st.line)} · {st.dist_to_line:.1%} above\n"
            f"1) Test? {q1}\n2) Violate? {q2}\n3) Dials? {q3}\n{rails}\n"
            + (f"Author targets: {tg} (letter {st.author.get('as_of')})" if tg else ""))


# ── persistence ──────────────────────────────────────────────────────────

def state_payload(st: NorsemanState) -> dict[str, Any]:
    data = _jsonable(asdict(st))
    assert isinstance(data, dict)
    return data


def _jsonable(x: object) -> object:
    if isinstance(x, dict):
        return {k: _jsonable(v) for k, v in x.items()}
    if isinstance(x, list | tuple):
        return [_jsonable(v) for v in x]
    if isinstance(x, date):
        return x.isoformat()
    return x


def _existing_keys(session: Session) -> set[str]:
    rows = session.execute(
        select(Signal.payload).where(Signal.signal_type == SIGNAL_EVENT)
        .order_by(Signal.ts.desc()).limit(2000)
    ).scalars().all()
    return {p.get("key") for p in rows if isinstance(p, dict) and p.get("key")}


def emit_signals(session: Session, *, as_of: date | None = None
                 ) -> tuple[NorsemanState | None, list[dict[str, Any]]]:
    """Write today's state + any NEW events. Returns (state, new_events)."""
    st = read_state(session, as_of=as_of)
    if st is None:
        log.warning("norseman.no_data")
        return None, []
    ts = datetime.combine(st.as_of, datetime.min.time())
    has_state = session.execute(
        select(Signal.id).where(Signal.signal_type == SIGNAL_STATE, Signal.ts == ts)
    ).first()
    if not has_state:
        session.add(Signal(ts=ts, symbol=SYMBOL, signal_type=SIGNAL_STATE,
                           payload=state_payload(st), confidence=None))
    seen = _existing_keys(session)
    new = [e for e in detect_events(st) if e["key"] not in seen]
    for e in new:
        session.add(Signal(ts=ts, symbol=SYMBOL, signal_type=SIGNAL_EVENT,
                           payload={**e, "as_of": st.as_of.isoformat()}, confidence=None))
    return st, new
