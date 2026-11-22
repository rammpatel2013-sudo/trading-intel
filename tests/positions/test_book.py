"""OI-anchored position book (2026-11-22). The build case is the Dec 31 7,250P (Oct 9-13 2026: OI 633 -> 28,166,
customers bought - VolSignals: ~27.2k customer long, ~21% of dealer short gamma)."""

from datetime import date

from trading_intel.positions import book as B
from trading_intel.scheduler.jobs import pos_book as J

D31 = date(2026, 12, 31)
K = ("SPXW", D31, 7250.0, "P")


def test_first_sight_oi_is_seed_and_never_signed():
    ln = B.step(None, 633, 633, lean=0.9, weight=1.0)
    assert ln.pos == 0 and ln.seed_oi == 633 and B.confidence(ln) == 0


def test_full_tape_build_signs_the_opened_size():
    ln = B.step(None, 633, 633, 0, 0)
    lean, w = B.session_lean(buy=9500, sell=200, d_oi=9716, ivl=0.5)
    ln = B.step(ln, 633, 10349, lean, w)
    assert 9000 < ln.pos < 9716 and ln.pos <= ln.oi
    assert 0.85 < B.confidence(ln) <= 1


def test_thin_tape_falls_back_to_iv_and_counts_less():
    lean_thin, w_thin = B.session_lean(buy=1500, sell=477, d_oi=9716, ivl=0.5)
    lean_full, w_full = B.session_lean(buy=9500, sell=200, d_oi=9716, ivl=0.5)
    assert 0.4 < lean_thin < lean_full and w_thin < w_full


def test_closes_shrink_and_position_never_exceeds_oi():
    ln = B.Line(pos=20000, known=20000, oi=28000, seed_oi=0)
    ln = B.step(ln, 28000, 14000, lean=-1.0, weight=1.0)  # customers sold 14k: closing their longs
    assert ln.pos == 6000 and ln.known == 10000
    ln = B.step(ln, 14000, 3000, lean=0.0, weight=0.0)  # unsigned closes: proportional
    assert abs(ln.pos - 6000 * 3000 / 14000) < 1e-6 and abs(ln.pos) <= ln.oi


def test_missed_day_cannot_drift():
    ln = B.Line(pos=3000, known=3000, oi=3000)
    ln = B.step(ln, 3000, 500, lean=0.0, weight=0.0)  # tape hole: OI fell, side unknown
    assert ln.pos == 500 and ln.oi == 500


def test_dealer_gamma_sign_and_share():
    rows = [
        {"key": "a", "mm_gamma_usd_1pct": B.gamma_usd_1pct(-27200, 0.00073, 6911)},
        {"key": "b", "mm_gamma_usd_1pct": -3.0e9},
        {"key": "c", "mm_gamma_usd_1pct": 1.0e9},
    ]
    assert rows[0]["mm_gamma_usd_1pct"] < 0  # customers long puts -> dealers short gamma
    sh = B.short_gamma_shares(rows)
    assert set(sh) == {"a", "b"} and abs(sum(sh.values()) - 1) < 1e-9 and 0.2 < sh["a"] < 0.3


def test_compute_session_end_to_end_pure():
    t = date(2026, 10, 9)
    book_t = {
        K: {"oi": 633.0, "iv": 0.184, "gamma": 0.00037},
        ("SPXW", D31, 7700.0, "P"): {"oi": 1000.0, "iv": 0.16, "gamma": 0.0004},
    }
    book_n = {
        K: {"oi": 10349.0, "iv": 0.188, "gamma": 0.00036},
        ("SPXW", D31, 7700.0, "P"): {"oi": 1000.0, "iv": 0.16, "gamma": 0.0004},
    }
    tape = {K: {"buy": 9000.0, "sell": 300.0, "prints": 40, "clips": 35, "premium": 6.0e7}}
    rows = J.compute_session(t, book_t, book_n, tape, [], {}, 7700.0)
    r = {x["key"]: x for x in rows}[K]
    assert (
        r["d_oi"] == 9716
        and r["seed_oi"] == 633
        and r["cust_pos"] > 8500
        and r["mm_gamma_usd_1pct"] < 0
    )
    sight = J.find_players(t, rows, tape, [], [], 7700.0)
    assert [s["player_slug"] for s in sight] == ["year-end-put-whale"] and r[
        "player_slug"
    ] == "year-end-put-whale"


def test_paired_legs_become_the_weekly_seller_and_fix_the_book():
    """10/13 2026: Nov 27 6,400P +58.9k and 6,500P +60.8k opened together, IV said 'bought' on both legs. Paired
    -> 100-wide vertical ~16% OTM ~45 DTE -> Weekly Put-Spread Seller -> dear 6,500 customer SHORT, 6,400 LONG.
    """
    t, e = date(2026, 10, 13), date(2026, 11, 27)
    k1, k2 = ("SPXW", e, 6500.0, "P"), ("SPXW", e, 6400.0, "P")
    atm = ("SPXW", e, 7700.0, "P")
    bt = {
        k1: {"oi": 1000.0, "iv": 0.30, "gamma": 1e-4},
        k2: {"oi": 900.0, "iv": 0.32, "gamma": 1e-4},
        atm: {"oi": 5000.0, "iv": 0.17, "gamma": 4e-4},
    }
    bn = {
        k1: {"oi": 61773.0, "iv": 0.31, "gamma": 1e-4},
        k2: {"oi": 59798.0, "iv": 0.33, "gamma": 1e-4},
        atm: {"oi": 5000.0, "iv": 0.17, "gamma": 4e-4},
    }
    rows, sight = J.session_book(t, bt, bn, {}, [], [], {}, 7700.0)
    assert [s["player_slug"] for s in sight] == ["weekly-putspread-seller"] and sight[0][
        "side"
    ] == -1
    r = {x["key"]: x for x in rows}
    assert r[k1]["cust_pos"] < -50000 and r[k2]["cust_pos"] > 50000
    assert r[k1]["player_slug"] == r[k2]["player_slug"] == "weekly-putspread-seller"
