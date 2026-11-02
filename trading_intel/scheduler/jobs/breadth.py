"""Scheduled job (daily, after cash close): market breadth → ``breadth_snapshots``.

DSM 18:30 ET (moved from 16:30 on 2026-10-29: at 16:30 the vendor bars and our own
SPX bar were still the previous session's). The row is skipped, not mis-dated, when
the constituent bars are not from today.

Pulls S&P-500 constituent EOD closes through the CVForge FMP passthrough (existing
vendor — ADR-004, rule 1), computes the S&P-wide breadth suite (``market.breadth``:
%-above-50/200-MA, advancers/decliners, new-highs/lows), then adds the regime half:

* the CUMULATIVE Advance-Decline line (yesterday's level + today's net adv),
* the McClellan oscillator + summation index (from the banked net-adv history),
* the Norseman **Bull/Bear Line** = 0.90 × the SPX all-time INTRADAY high (real
  SPX bars; SPY ratio-scaled only if SPX lags), and
* the A-D-line-vs-price **divergence** read (state + duration).

Degrades gracefully: if the constituent feed is slow / quota-limited the breadth
columns land ``None`` but the Bull/Bear Line (which only needs our own SPY series)
still persists. Idempotent upsert on (ts, source) (rule 5). Descriptive only
(rule 4).

Manual run:
    python -m trading_intel.scheduler.jobs.breadth
"""

from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_intel.config import Settings, get_settings
from trading_intel.market import breadth as bm
from trading_intel.memory.models import BreadthSnapshot
from trading_intel.timeutils import eastern_now, is_trading_session

log = structlog.get_logger(__name__)

_SOURCE = "fmp_sp500"
_HIST = 80  # prior snapshots pulled for McClellan / divergence context


def _spx_line_inputs(session: Session, as_of=None) -> tuple[list[float], float | None]:
    """(daily SPX intraday highs, ``as_of``'s SPX close) for the Bull/Bear Line.

    Norseman's line is 0.90 × the all-time INTRADAY high (verified 3/3 against his
    published lines, 2026-09-27) — not the weekly close. Uses real SPX bars, extended
    with ratio-scaled SPY when the SPX series lags (``strategies.norseman_regime``).
    The close is ``None`` unless the newest bar IS ``as_of``: a stale bar must not be
    filed under today (2026-10-29 fix — the 10/23 row carried the 10/22 close).
    """
    from trading_intel.strategies.norseman_regime import spx_bars

    as_of = as_of or eastern_now().date()
    bars = spx_bars(session, as_of=as_of)
    close = bars[-1].close if bars and bars[-1].d == as_of else None
    if bars and close is None:
        log.warning("breadth.spx_bar_stale", newest=str(bars[-1].d), as_of=str(as_of))
    return [b.high for b in bars], close


def _history(session: Session, cols, as_of, *filters) -> list:
    """The newest ``_HIST`` banked rows before ``as_of``, oldest → newest.

    Was ``order_by(ts).limit(_HIST)`` = the OLDEST 80 rows: once the A-D backfill
    added a year of history, McClellan was fed rows from 2025 plus today.
    """
    rows = session.execute(
        select(*cols)
        .where(BreadthSnapshot.source == _SOURCE, BreadthSnapshot.ts < as_of, *filters)
        .order_by(BreadthSnapshot.ts.desc())
        .limit(_HIST)
    ).all()
    return list(reversed(rows))


_SMALL_SOURCE = "fmp_sp600"
_SMALL_ETF = "IJR"  # iShares Core S&P Small-Cap — holdings = the S&P 600


def bank_small_caps(session: Session, client: object, as_of) -> dict | None:
    """S&P 600 advance/decline + cumulative A-D (Norseman's first rail to roll, N4).

    Universe = IJR holdings; day change from FMP batch-quote (~6 calls, no history
    pull). Banks a minimal ``breadth_snapshots`` row under source ``fmp_sp600``.
    Best-effort: returns None (and logs) on any failure.
    """
    from trading_intel.market import breadth_history as bh

    try:
        syms = bh.holdings_any(client, _SMALL_ETF)
        if len(syms) < 200:
            log.warning("breadth.small_caps_universe_thin", n=len(syms))
            return None
        adv, dec, n = bh.batch_change_counts(client, syms)
        if n < 200:
            log.warning("breadth.small_caps_quotes_thin", n=n)
            return None
    except Exception as exc:  # noqa: BLE001
        log.warning("breadth.small_caps_failed", error=str(exc))
        return None
    prev = session.execute(
        select(BreadthSnapshot.ad_line)
        .where(BreadthSnapshot.source == _SMALL_SOURCE, BreadthSnapshot.ts < as_of)
        .order_by(BreadthSnapshot.ts.desc())
    ).scalars().first()
    values = {"ts": as_of, "source": _SMALL_SOURCE, "advancers": adv, "decliners": dec,
              "net_adv": adv - dec, "ad_line": bm.ad_line_next(prev, adv, dec),
              "n_constituents": n}
    stmt = pg_insert(BreadthSnapshot).values(values)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_breadth_ts_source",
        set_={c: stmt.excluded[c] for c in values if c not in ("ts", "source")},
    )
    session.execute(stmt)
    session.commit()
    log.info("breadth.small_caps_banked", ts=str(as_of), n=n, net=adv - dec)
    return values


def run(session: Session, settings: Settings, *, client: object | None = None) -> dict:
    """Compute + bank one breadth row for today. Returns the persisted values."""
    as_of = eastern_now().date()
    if not is_trading_session(as_of):
        # Weekend/holiday runs would re-bank Friday's stale constituent counts as
        # a new session and cumulate them into ad_line. Skip, don't upsert.
        log.info("breadth.skipped_non_session", as_of=str(as_of))
        return {"skipped": "not_a_trading_session", "as_of": str(as_of)}

    # 1) constituent breadth (best-effort — never crashes the row)
    cvforge = client
    if cvforge is None:
        from trading_intel.clients.cvforge import CVForgeClient

        cvforge = CVForgeClient(settings)
    closes: dict[str, list[float]] = {}
    last_dates: dict[str, str] = {}
    try:
        syms = bm.sp500_symbols(cvforge)
        if syms:
            closes = bm.fetch_closes(cvforge, syms, last_dates=last_dates)
    except Exception as exc:  # noqa: BLE001 — degrade to Bull/Bear-Line-only
        log.warning("breadth.constituent_fetch_failed", error=str(exc))
    data_day = bm.data_session(last_dates)
    if closes and data_day != as_of.isoformat():
        # The constituent bars are from an earlier session (vendor not updated yet).
        # Filing them under today is how the 10/23 row came to hold 10/22's breadth,
        # and a cumulative A-D line can't be patched afterwards by the next run.
        # Skip; scripts/rebuild_breadth_history_20261029.py fills any missed day.
        log.error("breadth.source_stale", data_day=data_day, as_of=str(as_of))
        return {"skipped": "source_stale", "as_of": str(as_of), "data_day": data_day,
                "small_caps": bank_small_caps(session, cvforge, as_of)}
    b = bm.compute_breadth(closes) if closes else None

    # 2) prior snapshot (STRICTLY before today) — for the A-D cumulation + McClellan
    prev = session.execute(
        select(BreadthSnapshot)
        .where(BreadthSnapshot.source == _SOURCE, BreadthSnapshot.ts < as_of)
        .order_by(BreadthSnapshot.ts.desc())
    ).scalars().first()

    # 3) Norseman Bull/Bear Line = 0.90 × SPX all-time intraday high
    highs, spx_close = _spx_line_inputs(session, as_of)
    bbl = bm.bull_bear_line(highs)
    above_bbl = (spx_close > bbl) if (spx_close is not None and bbl is not None) else None

    # 4) net advances + cumulative A-D line
    net_adv = (b.advancers - b.decliners) if b else None
    prev_ad = prev.ad_line if prev else None
    ad_line = (
        bm.ad_line_next(prev_ad, b.advancers, b.decliners) if b else prev_ad
    )

    # 5) McClellan from banked net-adv history + today
    hist_net = [r[0] for r in _history(session, (BreadthSnapshot.net_adv,), as_of,
                                       BreadthSnapshot.net_adv.isnot(None))]
    net_series = list(hist_net) + ([net_adv] if net_adv is not None else [])
    mcc_osc, mcc_sum = bm.mcclellan(net_series, prev.mcclellan_sum if prev else None)

    # 6) divergence: banked (spx_close, ad_line) history + today
    hist_rows = _history(
        session, (BreadthSnapshot.spx_close, BreadthSnapshot.ad_line), as_of,
        BreadthSnapshot.spx_close.isnot(None), BreadthSnapshot.ad_line.isnot(None),
    )
    price_series = [float(r[0]) for r in hist_rows]
    ad_series = [float(r[1]) for r in hist_rows]
    if spx_close is not None and ad_line is not None:
        price_series.append(float(spx_close))
        ad_series.append(float(ad_line))
    div = bm.breadth_divergence(price_series, ad_series)

    values = {
        "ts": as_of,
        "source": _SOURCE,
        "advancers": b.advancers if b else None,
        "decliners": b.decliners if b else None,
        "net_adv": net_adv,
        "ad_line": ad_line,
        "new_highs": b.new_highs if b else None,
        "new_lows": b.new_lows if b else None,
        "pct_above_50": b.pct_above_50 if b else None,
        "pct_above_200": b.pct_above_200 if b else None,
        "mcclellan_osc": mcc_osc,
        "mcclellan_sum": mcc_sum,
        "n_constituents": b.n if b else None,
        "spx_close": spx_close,
        "bull_bear_line": bbl,
        "above_bbl": above_bbl,
        "divergence_state": div.get("state"),
        "divergence_len": div.get("length"),
    }

    stmt = pg_insert(BreadthSnapshot).values(values)
    update_cols = {c: stmt.excluded[c] for c in values if c not in ("ts", "source")}
    stmt = stmt.on_conflict_do_update(
        constraint="uq_breadth_ts_source", set_=update_cols
    )
    session.execute(stmt)
    session.commit()
    values["small_caps"] = bank_small_caps(session, cvforge, as_of)
    log.info(
        "breadth.banked",
        ts=str(as_of),
        n=values["n_constituents"],
        bbl=bbl,
        above_bbl=above_bbl,
        div=div.get("state"),
    )
    return values


def main() -> None:
    """Manual/scheduled entrypoint."""
    from trading_intel.memory.db import make_session_factory

    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    settings = get_settings()
    session_factory = make_session_factory(settings)
    with session_factory() as session:
        run(session, settings)


if __name__ == "__main__":
    main()
