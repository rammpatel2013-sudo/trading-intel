"""pos_book + tape_health + prune_tas_prints archive on a scratch SQLite DB (2026-11-22)."""

import gzip
from datetime import date, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import trading_intel.memory.models_equity
import trading_intel.memory.models_flow
import trading_intel.memory.models_positions  # noqa: F401
from trading_intel.memory.models import Base, OiChainDaily, OiChainEod, TasPrint
from trading_intel.memory.models_positions import (
    FlowPlayer,
    FlowPlayerSighting,
    PosBookDaily,
    TapeCoverageDaily,
)
from trading_intel.scheduler.jobs import pos_book, prune_tas_prints, tape_health

D31 = date(2026, 12, 31)
DAYS = [date(2026, 10, 9), date(2026, 10, 12), date(2026, 10, 13)]
OI = {DAYS[0]: 633, DAYS[1]: 10349, DAYS[2]: 18178}


@pytest.fixture()
def s():
    eng = create_engine("sqlite://")
    names = (
        "oi_chain_eod",
        "tas_prints",
        "pos_packages",
        "oi_chain_daily",
        "equity_bars",
        "pos_book_daily",
        "tape_coverage_daily",
        "flow_players",
        "flow_player_sightings",
    )
    Base.metadata.create_all(
        eng, tables=[Base.metadata.tables[n] for n in names if n in Base.metadata.tables]
    )
    with Session(eng) as ses:
        yield ses


def _book(s):
    for d in DAYS:
        s.add(OiChainDaily(symbol="SPX", ts=d, source="convex", spot=7700.0))
    for d in DAYS:
        for k, oi in ((7250.0, OI[d]), (7700.0, 1000)):
            s.add(
                OiChainEod(
                    symbol="SPX",
                    ts=datetime(d.year, d.month, d.day, 8, 35),
                    expiry=D31,
                    strike=k,
                    cp="P",
                    oi=oi,
                    iv=0.184 + 0.002 * DAYS.index(d) if k == 7250 else 0.16,
                    gamma=0.00037,
                    source="convex_am",
                )
            )
    n = 0
    for d in DAYS[:2]:
        for i in range(30):
            n += 1
            s.add(
                TasPrint(
                    captured_at=datetime(d.year, d.month, d.day, 15),
                    ts=datetime(d.year, d.month, d.day, 15, i),
                    trade_date=d,
                    symbol=f".SPXW{n}",
                    root="SPXW",
                    expiry=D31,
                    strike=7250.0,
                    cp="P",
                    side="buy",
                    price=62.0,
                    size=300,
                    quote_loc="at_ask",
                    source="convex",
                )
            )
    s.commit()


def test_pos_book_backfill_signs_the_whale_and_names_it(s):
    _book(s)
    out = pos_book.run(s, backfill=True, telegram=False)
    assert list(out) == ["2026-10-09", "2026-10-12"]
    rows = {(r.date, r.strike): r for r in s.scalars(select(PosBookDaily))}
    r1, r2 = rows[(DAYS[0], 7250.0)], rows[(DAYS[1], 7250.0)]
    assert (
        r1.seed_oi == 633 and r1.d_oi == 9716 and r1.cust_pos > 8000
    )  # 9,000 lots bought on full tape
    assert r2.d_oi == 7829 and r2.cust_pos > r1.cust_pos and r2.mm_gamma_usd_1pct < 0
    assert r2.player_slug == "year-end-put-whale"
    seen = {x.player_slug for x in s.scalars(select(FlowPlayerSighting))}
    assert seen == {"year-end-put-whale"}
    pl = {p.slug: p for p in s.scalars(select(FlowPlayer))}
    assert (
        pl["year-end-put-whale"].n_sightings == 2
        and pl["year-end-put-whale"].name == "Year-End Put Whale"
    )
    assert pl["jpm-collar"].status == "named" and pl["jpm-collar"].n_sightings == 0
    pos_book.run(s, backfill=True, telegram=False)  # re-runnable: no duplicates
    assert len(list(s.scalars(select(PosBookDaily)))) == len(rows)
    pos_book.name_player(s, "year-end-put-whale", "Big Put Guy")
    pos_book.refresh_players(s)
    assert (
        s.scalars(select(FlowPlayer).where(FlowPlayer.slug == "year-end-put-whale")).one().name
        == "Big Put Guy"
    )
    txt = pos_book.card(
        DAYS[1],
        [
            dict(r.__dict__, key=(r.root, r.expiry, r.strike, r.cp))
            for r in s.scalars(select(PosBookDaily).where(PosBookDaily.date == DAYS[1]))
        ],
        [],
        {"year-end-put-whale": "Big Put Guy"},
        [],
        None,
    )
    assert "Dec 31 7250P" in txt and "Big Put Guy" in txt and "100%" in txt


def test_tape_health_flags_hole_and_thin(s, monkeypatch):
    _book(s)
    monkeypatch.setattr(tape_health, "eastern_today", lambda: date(2026, 10, 13))
    monkeypatch.setattr(tape_health, "eastern_now", lambda: datetime(2026, 10, 13, 17, 30))
    out = {r["date"]: r for r in tape_health.run(s, days=3, telegram=False)}
    assert (
        out[DAYS[0]]["status"] == "hole" and "09:30" in out[DAYS[0]]["empty_rth_slots"]
    )  # prints only 15:00-15:30
    assert out[DAYS[2]]["status"] == "hole" and out[DAYS[2]]["prints"] == 0
    assert len(list(s.scalars(select(TapeCoverageDaily)))) == 3
    assert tape_health.classify(1000, 10000, None, [])[2] == "hole"
    assert tape_health.classify(3000, 10000, None, [])[2] == "thin"
    assert tape_health.classify(9000, None, 10000, [])[2] == "ok"
    assert "15:30" not in tape_health.rth_slots(date(2026, 11, 27))


def test_prune_archives_before_delete_and_keeps_a_year(s, tmp_path):
    _book(s)
    assert (
        prune_tas_prints.run(s, retention_days=30, now=datetime(2027, 1, 30), archive_dir=tmp_path)
        == 0
    )  # floor 365
    n = prune_tas_prints.run(
        s, retention_days=30, now=datetime(2027, 1, 30), archive_dir=tmp_path, force=True
    )
    assert n == 60 and not list(s.scalars(select(TasPrint)))
    f = tmp_path / "2026" / "tas_prints_2026-10-09.csv.gz"
    with gzip.open(f, "rt") as fh:
        assert sum(1 for _ in fh) == 31  # header + 30 prints
