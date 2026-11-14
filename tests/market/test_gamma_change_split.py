"""Tao T3 gamma split (2026-11-13 overnight) with a fake profile function."""
from datetime import date

from trading_intel.market.gamma_split import plain, split


def _prof(book, spot, base):
    def g(s):  # gamma rises with spot, two expiries
        return {"2026-11-14": 0.5 * base + 0.001 * (s - 7500), "2026-11-20": 0.5 * base}
    return book, spot, g


BOOKS = {date(2026, 11, 13): _prof("2026-11-13", 7560.0, -2.0), date(2026, 11, 12): _prof("2026-11-12", 7590.0, -1.0)}


def fake_profile(session, symbol, *, as_of=None, spot_ref=None, greeks=()):
    key = max(d for d in BOOKS if as_of is None or d <= as_of)
    book, spot, g = BOOKS[key]
    S = spot_ref if spot_ref is not None else spot
    ladder = [S - 1, S, S + 1]
    by = {k: [g(x)[k] for x in ladder] for k in g(S)}
    tot = [sum(by[k][i] for k in by) for i in range(3)]
    return {"found": True, "as_of": book, "spot": spot, "profiles": {"spot_ref": ladder, "gamma": {"all": tot, "by_expiry": by}}}


def test_split_adds_up():
    r = split(None, "SPX", today=date(2026, 11, 13), profile=fake_profile)
    assert r["found"] and r["prev_book"] == "2026-11-12"
    assert abs(r["spot_walk"] + r["book_change"] - r["change"]) < 1e-9
    assert r["next_expiry"] == "2026-11-14" and 0 < r["next_expiry_share"] < 1
    assert "came from price moving" in plain(r)
