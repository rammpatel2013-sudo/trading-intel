"""SPX spread packages (2026-11-21): fills, side once per package, open / close / roll from the OI change.
The roll case is the 11/19 VolSignals one: Nov 27 6975/7000 put spread bought back, Jan 15 6775/6800 sold."""
import csv
import gzip
from datetime import date, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from trading_intel.memory.models import Base
from trading_intel.memory.models_positions import PosPackage
from trading_intel.positions import packages as P
from trading_intel.scheduler.jobs import pos_packages as J

N27, J15 = date(2026, 11, 27), date(2027, 1, 15)


def _p(t, root, exp, k, side, px, size, loc):
    return {"ts": datetime(2026, 11, 19, 14, 18, t), "root": root, "expiry": exp, "strike": float(k), "cp": "P",
            "side": side, "price": px, "size": size, "quote_loc": loc}


def _roll():
    out = []
    for t, size in ((38, 117), (39, 79), (52, 1347)):
        # per-leg aggressor tags disagree (as on the real tape); quote position carries the side
        out += [_p(t, "SPXW", N27, 7000, "buy", 43.2, size, "in_hi"), _p(t, "SPXW", N27, 6975, "buy", 39.0, size, "outside"),
                _p(t, "SPX", J15, 6800, "sell", 114.6, size, "in_lo"), _p(t, "SPX", J15, 6775, "buy", 110.4, size, "in_hi")]
    return out


DOI = {("SPXW", N27, 6975.0, "P"): -4434, ("SPXW", N27, 7000.0, "P"): -2509,
       ("SPX", J15, 6775.0, "P"): 7128, ("SPX", J15, 6800.0, "P"): 5010}


def test_fill_needs_same_second_and_size():
    a = [_p(1, "SPXW", N27, 7000, "buy", 43.0, 10, "in_hi"), _p(1, "SPXW", N27, 6975, "sell", 39.0, 11, "in_lo")]
    assert P.events_for_day(a) == []                                  # sizes differ: not one fill
    a[1]["size"] = 10
    ev = P.events_for_day(a)
    assert len(ev) == 1 and ev[0].structure == "vertical" and ev[0].side == 1 and ev[0].size == 10


def test_roll_side_from_quote_position_not_leg_tags():
    ev = {e.legs[0][1]: e for e in P.events_for_day(_roll())}
    assert ev[N27].side == 1 and ev[J15].side == -1                  # bought back near, sold far
    assert ev[N27].size == ev[J15].size == 117 + 79 + 1347
    assert abs(ev[N27].net - 4.2) < 0.01 and abs(ev[J15].net - 4.2) < 0.01
    assert ev[N27].legs[0][2] == 7000.0                              # dear leg first


def test_roll_classified_from_oi_change():
    rows = {r["ev"].legs[0][1]: r for r in P.classify(P.events_for_day(_roll()), DOI)}
    assert rows[N27]["action"] == "roll_close" and rows[J15]["action"] == "roll_open"
    assert rows[J15]["roll_key"] == rows[N27]["ev"].key


def test_unknown_without_oi_and_plain_open():
    assert {r["action"] for r in P.classify(P.events_for_day(_roll()), {})} == {"unknown"}
    one = [p for p in _roll() if p["expiry"] == J15]
    rows = P.classify(P.events_for_day(one), DOI)
    assert [r["action"] for r in rows] == ["open"]


def test_calendar_and_utc_clock():
    a = [_p(5, "SPXW", N27, 7100, "buy", 30.0, 20, "in_hi"), _p(5, "SPXW", J15, 7100, "buy", 80.0, 20, "in_hi")]
    ev = P.events_for_day(a)
    assert ev[0].structure == "calendar" and ev[0].legs[0][1] == J15   # dear leg = far expiry
    assert P.to_et(datetime(2026, 11, 19, 19, 18), date(2026, 11, 19)) == datetime(2026, 11, 19, 14, 18)
    assert P.to_et(datetime(2026, 11, 20, 14, 18), date(2026, 11, 20)) == datetime(2026, 11, 20, 14, 18)


def _od(path, oi):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["record", "iid", "root", "expiration", "strike", "right", "open_interest"])
        for i, ((root, exp, k, cp), v) in enumerate(oi.items()):
            w.writerow(["contract", i, root, exp.isoformat(), f"{k:.3f}", "put" if cp == "P" else "call", ""])
            w.writerow(["oi", i, "", "", "", "", v])


def test_job_rows_oi_files_and_upsert(tmp_path):
    base = {k: 10000.0 for k in DOI}
    _od(tmp_path / "20261119" / "SPX.csv.gz", base)
    _od(tmp_path / "20261120" / "SPX.csv.gz", {k: base[k] + v for k, v in DOI.items()})
    doi = J.oi_change(date(2026, 11, 19), date(2026, 11, 20), tmp_path)
    assert doi[("SPXW", N27, 6975.0, "P")] == -4434
    assert J.oi_change(date(2026, 11, 19), date(2026, 11, 21), tmp_path) == {}     # next file missing
    rows = J.rows_for_day(date(2026, 11, 19), _roll(), doi, datetime(2026, 11, 21, 3, 0))
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng, tables=[PosPackage.__table__])
    with Session(eng) as s:
        assert J.save(s, rows) == 2
        rows[0]["action"] = "unknown"
        J.save(s, rows)                                              # re-run: same keys, updated in place
        got = s.scalars(select(PosPackage)).all()
        assert len(got) == 2 and {g.action for g in got} >= {"unknown"}


def test_box_is_skipped():
    d = date(2026, 12, 18)
    a = [{"ts": datetime(2026, 11, 19, 15, 21, 0), "root": "SPX", "expiry": d, "strike": float(k), "cp": cp, "side": "buy",
          "price": px, "size": 2000, "quote_loc": "mid"} for k, cp, px in ((7000, "C", 400.0), (8000, "C", 80.0),
                                                                           (7000, "P", 300.0), (8000, "P", 976.0))]
    assert P.events_for_day(a) == []
