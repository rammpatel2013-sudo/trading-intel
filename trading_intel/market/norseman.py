"""Norseman Market Timing (NMT) regime math — pure, numbers-in → numbers-out.

Reverse-engineered from NMT letters 63–70 and verified against our own SPX
``quotes_daily`` series (2026-09-27):

* **Bull/Bear Line (B/BL)** = 0.90 × the all-time **intraday high** to date. It only
  ratchets up. Verified 3/3: 6,858.81 = .9×7,620.90 (6/2/26 high), 7,014.31 =
  .9×7,793.68 (8/5), 7,035.03 = .9×7,816.70 (8/13).
* **Test** = price taps the line: an intraday low within ``TEST_BAND`` (1%) of it
  (3/30/26 low 6,316.91 vs line 6,302.05 = the anchor he uses). Consecutive tap days
  form one test episode; the anchor is the episode's lowest low.
* **Violation** ("a 10% close") = a WEEKLY close below the line. One can be survived
  (a test); two in the same cyclical bull = terminal.
* **Clock** = NYSE sessions since the last test anchor. A second test is not
  "permitted" before session ``CLOCK_OPEN_SESSION`` (128 ≈ 6 months). 9/25/26 = S124.
* **Dials / ASSESS** = breadth rails (A-D line, RSP = the average stock, IWM = small
  caps) must confirm a new price high. A rail that peaked ≥ ``CONFIRM_SESSIONS``
  before a new price high and is still below that peak = a divergence; the session
  gap is the warning (2018: 21, 2024-25: 53 sessions).

Descriptor math only (CLAUDE.md rule 4) — ``strategies.norseman_regime`` turns it
into signals.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

BBL_PCT = 0.10
TEST_BAND = 0.01  # low within 1% of the line = a tap
EPISODE_GAP = 10  # tap days ≤ this many sessions apart = one test episode
CLOCK_OPEN_SESSION = 128
CONFIRM_SESSIONS = 5  # rail peak within this many sessions of the price high = confirmed
ASSESS_MATURE_GAP = 14  # "a top now needs at least 14 sessions of warning"


@dataclass(frozen=True, slots=True)
class Bar:
    d: date
    high: float
    low: float
    close: float


@dataclass(frozen=True, slots=True)
class LineDay:
    d: date
    ath: float  # all-time intraday high BEFORE this session
    line: float  # B/BL in force for this session


def line_series(bars: Sequence[Bar], *, pct: float = BBL_PCT) -> list[LineDay]:
    """B/BL in force each session = (1-pct) × ATH of all PRIOR sessions."""
    out: list[LineDay] = []
    ath = 0.0
    for b in bars:
        if ath > 0:
            out.append(LineDay(b.d, ath, (1.0 - pct) * ath))
        ath = max(ath, b.high)
    return out


def current_line(bars: Sequence[Bar], *, pct: float = BBL_PCT) -> tuple[float, float] | None:
    """(ATH incl. today, B/BL after today's session) — the line for next session."""
    highs = [b.high for b in bars if b.high is not None]
    if not highs:
        return None
    ath = max(highs)
    return ath, (1.0 - pct) * ath


@dataclass(frozen=True, slots=True)
class TestEpisode:
    start: date  # first tap day
    anchor: date  # lowest-low day = session 0 of the clock
    low: float
    line: float


def test_episodes(bars: Sequence[Bar], *, band: float = TEST_BAND,
                  gap: int = EPISODE_GAP) -> list[TestEpisode]:
    """Every B/BL test episode (taps ≤ ``gap`` sessions apart merge), oldest→newest."""
    lines = {ld.d: ld.line for ld in line_series(bars)}
    groups: list[list[tuple[int, Bar]]] = []
    last_i = -10_000
    for i, b in enumerate(bars):
        line = lines.get(b.d)
        if line is None or b.low > line * (1.0 + band):
            continue
        if not groups or i - last_i > gap:
            groups.append([])
        groups[-1].append((i, b))
        last_i = i
    out: list[TestEpisode] = []
    for g in groups:
        lo = min((b for _, b in g), key=lambda x: x.low)
        out.append(TestEpisode(start=g[0][1].d, anchor=lo.d, low=lo.low, line=lines[lo.d]))
    return out


def weekly_closes(bars: Sequence[Bar]) -> list[Bar]:
    """Last bar of each ISO week (the weekly close), oldest→newest."""
    by_week: dict[tuple[int, int], Bar] = {}
    for b in bars:
        iso = b.d.isocalendar()
        by_week[(iso[0], iso[1])] = b  # bars are oldest→newest, last one wins
    return [by_week[k] for k in sorted(by_week)]


def violations(bars: Sequence[Bar], *, since: date) -> list[tuple[date, float, float]]:
    """Weekly closes below the line after ``since`` → [(week-end date, close, line)].

    ``since`` should be the first weekly close back ABOVE the line of the current
    cyclical bull (see ``bull_count_start``) so the bear's own closes don't count.
    """
    lines = {ld.d: ld.line for ld in line_series(bars)}
    return [
        (w.d, w.close, lines[w.d])
        for w in weekly_closes(bars)
        if w.d > since and w.d in lines and w.close < lines[w.d]
    ]


def bull_count_start(bars: Sequence[Bar], *, bull_start: date) -> date | None:
    """First weekly close ABOVE the line on/after the author's cyclical buy date."""
    lines = {ld.d: ld.line for ld in line_series(bars)}
    for w in weekly_closes(bars):
        if w.d >= bull_start and w.d in lines and w.close >= lines[w.d]:
            return w.d
    return None


def sessions_between(session_dates: Sequence[date], start: date, end: date) -> int | None:
    """Count of sessions from ``start`` (S0) to ``end`` using the observed calendar."""
    ds = list(session_dates)
    try:
        return ds.index(end) - ds.index(start)
    except ValueError:
        return None


def project_session_date(last_date: date, sessions_ahead: int,
                         is_session: Callable[[date], bool]) -> date:
    """Calendar date ``sessions_ahead`` trading sessions after ``last_date``."""
    d = last_date
    n = 0
    while n < sessions_ahead:
        d += timedelta(days=1)
        if is_session(d):
            n += 1
    return d


@dataclass(slots=True)
class RailRead:
    """One breadth rail (A-D line / RSP / IWM) vs the latest price high."""

    name: str
    peak_date: date | None = None
    peak: float | None = None
    last: float | None = None
    pct_off_peak: float | None = None
    diverging: bool = False
    gap_sessions: int | None = None  # rail peak → price high, in sessions


@dataclass(slots=True)
class DialsRead:
    price_high_date: date | None
    rails: list[RailRead] = field(default_factory=list)

    @property
    def diverging(self) -> list[RailRead]:
        return [r for r in self.rails if r.diverging]

    @property
    def max_gap(self) -> int | None:
        gaps = [r.gap_sessions for r in self.diverging if r.gap_sessions is not None]
        return max(gaps) if gaps else None


def rail_read(name: str, series: Sequence[tuple[date, float]], *, since: date,
              price_high_date: date | None, session_dates: Sequence[date],
              confirm: int = CONFIRM_SESSIONS) -> RailRead:
    """Did this rail confirm the latest price high? ``series`` = (date, value)."""
    pts = [(d, float(v)) for d, v in series if v is not None and d >= since]
    rr = RailRead(name=name)
    if not pts:
        return rr
    rr.peak_date, rr.peak = max(pts, key=lambda p: (p[1], p[0]))
    rr.last = pts[-1][1]
    if rr.peak:
        rr.pct_off_peak = rr.last / rr.peak - 1.0 if rr.peak > 0 else None
    if price_high_date is None or rr.peak_date is None:
        return rr
    gap = sessions_between(session_dates, rr.peak_date, price_high_date)
    rr.gap_sessions = gap
    rr.diverging = bool(gap is not None and gap > confirm and rr.last < rr.peak)
    return rr
