"""RR builder: IV inversion, expiries, 25-delta read-out, cache (2026-11-13). No network."""
from datetime import date, timedelta

import numpy as np
import pandas as pd

from trading_intel.vol import rr_history as rh


def test_implied_vol_round_trip():
    k = np.array([80.0, 90.0, 100.0, 110.0])
    p, _ = rh.bs_price_delta(100.0, k, 30 / 365, 0.04, np.full(4, 0.42), "P")
    v = rh.implied_vols(100.0, k, 30 / 365, 0.04, p, "P")
    assert np.allclose(v, 0.42, atol=1e-4)


def test_monthly_expiries_third_friday_and_holiday():
    ex = rh.monthly_expiries(date(2026, 5, 1), date(2026, 7, 1))
    assert date(2026, 5, 15) in ex and date(2026, 6, 18) in ex and date(2026, 7, 17) in ex


def _smile(s, k):
    return 0.35 + 0.6 * np.clip(s / k - 1, -0.3, 0.3)  # puts (low strikes) richer than calls


def _bars_for(day, s, expiries):
    out = []
    for e in expiries:
        t = (e - day).days / 365
        for cp, ks in (("P", np.arange(70, 104, 2.5)), ("C", np.arange(96, 131, 2.5))):
            p, _ = rh.bs_price_delta(s, ks, t, rh.short_rate(day), _smile(s, ks), cp)
            out += [(e, cp, float(k), day, float(c)) for k, c in zip(ks, p)]
    return out


def test_rows_from_bars_reads_skew_and_picks_30_day_expiry():
    day = date(2026, 10, 20)
    rows = rh.rows_from_bars({day: 100.0}, _bars_for(day, 100.0, [date(2026, 11, 20), date(2026, 12, 18)]))
    spot, dte, ivp, ivc, atm = rows[day]
    assert dte == 31 and ivp > ivc + 0.05 and 0.3 < atm < 0.4


class _FakeClient:
    def __init__(self, days, s=100.0):
        self.calls, self.days, self.s = 0, days, s

    def aggs(self, ticker, *, frm, to, adjusted=None, **_):
        self.calls += 1
        sym_end = ticker[2:].lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        e = date(2000 + int(sym_end[:2]), int(sym_end[2:4]), int(sym_end[4:6]))
        cp, k = sym_end[6], int(sym_end[7:]) / 1000
        rows = []
        for d in self.days:
            if frm <= f"{d:%Y-%m-%d}" <= to and 0 < (e - d).days:
                p, _ = rh.bs_price_delta(self.s, np.array([k]), (e - d).days / 365, rh.short_rate(d),
                                         _smile(self.s, np.array([k])), cp)
                rows.append({"t": pd.Timestamp(d).value // 10**6 + 5 * 3600 * 1000, "c": float(p[0])})
        df = pd.DataFrame(rows)
        if len(df):
            df["ts"] = pd.to_datetime(df["t"], unit="ms")
        return df


def test_update_caches_and_only_pulls_new_sessions(tmp_path):
    days = [date(2026, 9, 21) + timedelta(days=i) for i in range(28) if (date(2026, 9, 21) + timedelta(days=i)).weekday() < 5]
    cli = _FakeClient(days)
    h = rh.RRHistory(cli, tmp_path, workers=2, per_min=100000)
    out = h.update("TEST", {d: 100.0 for d in days}, days[0])
    assert (tmp_path / f"TEST_{rh.CACHE_VERSION}.csv").exists() and out["ivp25"].notna().sum() >= len(days) - 2
    first = cli.calls
    h.update("TEST", {d: 100.0 for d in days}, days[0])
    assert cli.calls == first  # nothing new to pull


def test_two_step_grid_covers_2_5_dollar_strikes():
    ks = rh.strike_grid_multi(0.7 * 13, 1.02 * 15, 9, rh.strike_steps(15.0))
    assert {12.0, 12.5, 15.0, 17.5} <= set(ks)
