"""clean_book (2026-11-14): our own forward + IVs from the book's prices; vendor IVs ignored."""
from __future__ import annotations

from datetime import date, datetime

import numpy as np

from trading_intel.market import clean_book as cb

BOOK = datetime(2026, 11, 13, 8, 35)


def _rows(fwd: float, exp: date, *, iv_fn=lambda k: 0.15, source: str = "convex_am",
          vendor_iv: float | None = 0.0023, half_spread: float = 0.05) -> list[dict]:
    """Bid/ask from Black-76 at ``fwd``; the vendor IV column deliberately WRONG (the 11/13 Nov-18 7,860 call
    carried 0.23%)."""
    t = cb.years(BOOK, cb.settle_dt(exp, source.endswith("_set")))
    out = []
    for k in np.arange(7300.0, 8000.0, 5.0):
        for cp in ("C", "P"):
            m = float(cb.b76(fwd, np.array([k]), np.array([iv_fn(k)]), t, np.array([cp == "C"]))[0])
            bid, ask = (round(m - half_spread, 2), round(m + half_spread, 2)) if m > 0.1 else (None, None)
            out.append({"expiry": exp, "strike": float(k), "cp": cp, "oi": 1000, "bid": bid, "ask": ask,
                        "source": source, "iv": vendor_iv})
    return out


def test_forward_from_parity_and_ivs_from_prices_not_the_vendor():
    skew = lambda k: 0.15 - 0.6 * np.log(k / 7645.0)  # noqa: E731
    sms = cb.build_smiles(_rows(7645.0, date(2026, 11, 16), iv_fn=skew), BOOK)
    sm = sms[(date(2026, 11, 16), False)]
    assert abs(sm.forward - 7645.0) < 0.2 and sm.parity_spread < 0.5
    k = np.array([7550.0, 7645.0, 7750.0])
    assert np.allclose(np.interp(k, sm.strikes, sm.ivs), skew(k), atol=0.004)   # vendor said 0.23%
    assert abs(cb.atm_iv(sm) - 0.15) < 0.003


def test_am_settled_contracts_get_their_own_clock_and_smile():
    rows = _rows(7645.0, date(2026, 11, 20)) + _rows(7645.0, date(2026, 11, 20), source="convex_am_set")
    sms = cb.build_smiles(rows, BOOK)
    pm, am = sms[(date(2026, 11, 20), False)], sms[(date(2026, 11, 20), True)]
    assert am.t < pm.t and abs((pm.t - am.t) * 365 * 24 - 6.5) < 0.01            # 09:30 vs 16:00 settle
    assert abs(am.forward - 7645.0) < 0.5


def test_real_straddle_is_call_plus_put_mid_at_the_forward():
    sm = cb.build_smiles(_rows(7644.0, date(2026, 11, 13)), BOOK)[(date(2026, 11, 13), False)]
    strd, k = cb.straddle_mid(sm)
    assert k == 7645.0 and strd == sm.mids[(7645.0, "C")] + sm.mids[(7645.0, "P")]


def test_vendor_iv_disagreement_is_measured():
    rows = _rows(7645.0, date(2026, 11, 18))
    for r in rows:
        r["iv"] = 0.05 if r["cp"] == "C" else 0.33                            # the 11/13 pattern
    d = cb.iv_disagreement(rows, {date(2026, 11, 18): 7645.0})
    assert abs(d[date(2026, 11, 18)] - 28.0) < 0.01


def test_constant_maturity_and_gamma_sign():
    rows = _rows(7645.0, date(2026, 11, 16)) + _rows(7645.0, date(2026, 12, 18), iv_fn=lambda k: 0.17)
    sms = cb.build_smiles(rows, BOOK)
    v = cb.cm_atm_iv(sms, 14)
    assert 0.15 < v < 0.17
    for sm in sms.values():                # call-heavy book -> dealers long gamma (naive sign)
        sm.oi_call = {k: 3 * v for k, v in sm.oi_call.items()}
    grid = np.linspace(7600, 7700, 21)
    assert (cb.gamma_curve(sms, grid) > 0).all()
