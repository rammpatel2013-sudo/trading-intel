"""vol_fade EOD job - fake Cboe / Telegram / prices, SQLite, no network, no date dependence."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from trading_intel.memory.models import Signal
from trading_intel.scheduler.jobs import vol_fade as job

FIRE_DAY = date(2026, 11, 13)  # a Friday session


def _hist(n_days: int, end: date, vals: list[float]) -> list[tuple[date, float]]:
    days, d = [], end
    while len(days) < n_days:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return list(zip(sorted(days), vals, strict=True))


class FakeCboe:
    """History files end the day BEFORE ``today`` (they post late); quotes carry today."""

    def __init__(self, today_vix=19.0, today_v9=18.0, today_vvix=115.0, hist_has_today=False):
        vix = [18.0] * 5 + [26.0, 24.0, 22.0] + [20.0]
        rat = [0.9] * 5 + [1.05, 1.04, 1.02] + [0.95]
        end = FIRE_DAY if hist_has_today else FIRE_DAY - timedelta(days=1)
        n = len(vix) if hist_has_today else len(vix) - 1
        self.h = {
            "VIX": _hist(n, end, vix[:n]),
            "VIX9D": _hist(n, end, [v * r for v, r in zip(vix[:n], rat[:n], strict=True)]),
            "VVIX": _hist(n, end, [100.0] * n),
        }
        if hist_has_today:
            self.h["VIX9D"] = self.h["VIX9D"][:-1]  # VIX posted, VIX9D not yet
        self.q = {"VIX": today_vix, "VIX9D": today_v9}
        self.vv = today_vvix

    def index_history(self, name):
        return self.h[name]

    def term_structure(self):
        return dict(self.q)

    def vvix(self):
        return self.vv


class FakeTg:
    def __init__(self):
        self.sent = []

    def send_message(self, text, **_):
        self.sent.append(text)
        return True


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    Signal.__table__.create(engine)
    with Session(engine) as s:
        yield s


def test_fire_pushed_once(session, monkeypatch):
    monkeypatch.setattr(job, "is_trading_session", lambda d=None: True)
    tg = FakeTg()
    new = job.run(session, settings=object(), cboe=FakeCboe(), today=FIRE_DAY, tg=tg)
    assert new == ["FIRE"]
    assert (
        len(tg.sent) == 1
        and "FIRE" in tg.sent[0]
        and "strong" in tg.sent[0]
        and "SVXY" in tg.sent[0]
    )
    assert job.run(session, settings=object(), cboe=FakeCboe(), today=FIRE_DAY, tg=tg) == []
    assert len(tg.sent) == 1
    types = sorted(r.signal_type for r in session.execute(select(Signal)).scalars())
    assert types == ["VOL_FADE_FIRE", "VOL_FADE_STATE"]


def test_today_vix9d_filled_from_quote():
    rows = job.load_rows(FakeCboe(hist_has_today=True), FIRE_DAY)
    assert rows[-1][0] == FIRE_DAY and rows[-1][1] == 20.0
    assert rows[-1][2] == 18.0 and rows[-1][3] == 100.0  # VIX9D from the quote, VVIX from history


def test_non_session_skipped(session, monkeypatch):
    monkeypatch.setattr(job, "is_trading_session", lambda d=None: False)
    tg = FakeTg()
    assert job.run(session, settings=object(), cboe=FakeCboe(), today=FIRE_DAY, tg=tg) == []
    assert tg.sent == [] and session.execute(select(Signal)).first() is None


def test_no_close_today_writes_nothing(session, monkeypatch):
    monkeypatch.setattr(job, "is_trading_session", lambda d=None: True)
    cb = FakeCboe()
    cb.q = {}
    assert job.run(session, settings=object(), cboe=cb, today=FIRE_DAY, tg=FakeTg()) == []
    assert session.execute(select(Signal)).first() is None


def test_dry_run_writes_nothing(session, capsys):
    events = job.run(session, settings=object(), cboe=FakeCboe(), today=FIRE_DAY, dry_run=True)
    assert events == ["FIRE"] and "FIRE" in capsys.readouterr().out
    assert session.execute(select(Signal)).first() is None


class FakePrices:
    def daily_history(self, symbol, period="3mo"):
        d = pd.to_datetime(["2026-11-13", "2026-11-16", "2026-11-17"])
        return pd.DataFrame({"date": d, "open": [50.0, 51.0, 52.0], "close": [50.5, 52.0, 53.55]})


def test_trade_result_entry_is_next_open():
    assert (
        job.trade_result(FakePrices(), "2026-11-13", date(2026, 11, 17))
        == "SVXY 51.00 -> 53.55 = +5.0%"
    )
    assert job.trade_result(object(), "2026-11-13", date(2026, 11, 17)) is None


def test_history_line_uses_frozen_numbers():
    line = job._history_line("strong")
    assert "16 trades" in line and "+5.3%" in line and "49 trades" in line
