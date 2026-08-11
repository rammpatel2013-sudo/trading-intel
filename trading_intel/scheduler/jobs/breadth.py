"""Scheduled job (daily, after cash close): market breadth → ``breadth_snapshots``.

Pulls S&P-500 constituent EOD closes through the CVForge FMP passthrough (existing
vendor — ADR-004, rule 1), computes the S&P-wide breadth suite (``market.breadth``:
%-above-50/200-MA, advancers/decliners, new-highs/lows), then adds the regime half:

* the CUMULATIVE Advance-Decline line (yesterday's level + today's net adv),
* the McClellan oscillator + summation index (from the banked net-adv history),
* the Norseman **Bull/Bear Line** = 0.90 × running-max WEEKLY SPX-equivalent close,
  computed off the maintained SPY ``quotes_daily`` series (×10 — SPX quotes go
  stale), and
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
from trading_intel.memory.models import BreadthSnapshot, QuoteDaily
from trading_intel.timeutils import eastern_now

log = structlog.get_logger(__name__)

_SOURCE = "fmp_sp500"
_HIST = 80  # prior snapshots pulled for McClellan / divergence context


def _spx_equiv_weeklies(session: Session) -> tuple[list[float], float | None]:
    """Weekly SPX-equivalent closes (SPY×10) + the latest daily close, oldest→newest."""
    rows = session.execute(
        select(QuoteDaily.date, QuoteDaily.close)
        .where(QuoteDaily.symbol == "SPY")
        .order_by(QuoteDaily.date)
    ).all()
    dated = [(d, float(c) * 10.0) for d, c in rows if c is not None]
    weekly = bm.weekly_last_closes(dated)
    latest = dated[-1][1] if dated else None
    return weekly, latest


def run(session: Session, settings: Settings, *, client: object | None = None) -> dict:
    """Compute + bank one breadth row for today. Returns the persisted values."""
    as_of = eastern_now().date()

    # 1) constituent breadth (best-effort — never crashes the row)
    cvforge = client
    if cvforge is None:
        from trading_intel.clients.cvforge import CVForgeClient

        cvforge = CVForgeClient(settings)
    closes: dict[str, list[float]] = {}
    try:
        syms = bm.sp500_symbols(cvforge)
        if syms:
            closes = bm.fetch_closes(cvforge, syms)
    except Exception as exc:  # noqa: BLE001 — degrade to Bull/Bear-Line-only
        log.warning("breadth.constituent_fetch_failed", error=str(exc))
    b = bm.compute_breadth(closes) if closes else None

    # 2) prior snapshot (STRICTLY before today) — for the A-D cumulation + McClellan
    prev = session.execute(
        select(BreadthSnapshot)
        .where(BreadthSnapshot.source == _SOURCE, BreadthSnapshot.ts < as_of)
        .order_by(BreadthSnapshot.ts.desc())
    ).scalars().first()

    # 3) Norseman Bull/Bear Line from our own SPY series (always available)
    weekly, spx_close = _spx_equiv_weeklies(session)
    bbl = bm.bull_bear_line(weekly)
    above_bbl = (spx_close > bbl) if (spx_close is not None and bbl is not None) else None

    # 4) net advances + cumulative A-D line
    net_adv = (b.advancers - b.decliners) if b else None
    prev_ad = prev.ad_line if prev else None
    ad_line = (
        bm.ad_line_next(prev_ad, b.advancers, b.decliners) if b else prev_ad
    )

    # 5) McClellan from banked net-adv history + today
    hist_net = session.execute(
        select(BreadthSnapshot.net_adv)
        .where(
            BreadthSnapshot.source == _SOURCE,
            BreadthSnapshot.ts < as_of,
            BreadthSnapshot.net_adv.isnot(None),
        )
        .order_by(BreadthSnapshot.ts)
        .limit(_HIST)
    ).scalars().all()
    net_series = list(hist_net) + ([net_adv] if net_adv is not None else [])
    mcc_osc, mcc_sum = bm.mcclellan(net_series, prev.mcclellan_sum if prev else None)

    # 6) divergence: banked (spx_close, ad_line) history + today
    hist_rows = session.execute(
        select(BreadthSnapshot.spx_close, BreadthSnapshot.ad_line)
        .where(
            BreadthSnapshot.source == _SOURCE,
            BreadthSnapshot.ts < as_of,
            BreadthSnapshot.spx_close.isnot(None),
            BreadthSnapshot.ad_line.isnot(None),
        )
        .order_by(BreadthSnapshot.ts)
        .limit(_HIST)
    ).all()
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
