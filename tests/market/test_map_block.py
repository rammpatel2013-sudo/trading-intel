"""MAP block maths: book building, flip, walls, cones, hedge pressure, signed flow."""

from __future__ import annotations

import pytest

from trading_intel.market import map_block as mb


def _chain(spot: float = 6000.0) -> list[tuple]:
    """A symmetric toy book: calls above spot, puts below, expiries at 0 and 7 DTE.

    Flow shape matches what Friday's real chain showed: customers net SELL puts
    and net BUY calls.
    """
    rows: list[tuple] = []
    for dte in (0, 7):
        for k in range(5700, 6301, 50):
            moneyness = (k - spot) / spot
            call_delta = max(0.01, min(0.99, 0.5 - moneyness * 8))
            put_delta = -max(0.01, min(0.99, 0.5 + moneyness * 8))
            oi = 20_000 if k in (5900, 6100) else 4_000
            gxoi = 0.0005 * oi
            rows.append((float(k), "C", dte, oi, call_delta, 0.0005, 0.14, gxoi, 6.0, 4.0))
            rows.append((float(k), "P", dte, oi, put_delta, 0.0005, 0.16, gxoi, 3.0, 9.0))
    return rows


# ── book building ──────────────────────────────────────────────────────


def test_build_book_filters_and_windows():
    rows = _chain()
    near = mb.build_book(rows, dte_max=7)
    same = mb.build_book(rows, dte_max=0)
    assert near.n == len(rows)
    assert same.n == len(rows) // 2
    assert (near.iv > 0).all()
    assert set(near.sign.tolist()) == {1.0, -1.0}


def test_build_book_drops_unusable_rows():
    rows = [
        (6000.0, "C", 0, 100, 0.5, 0.001, 0.14, 0.1, 1.0, 1.0),  # good
        (None, "C", 0, 100, 0.5, 0.001, 0.14, 0.1, 1.0, 1.0),    # no strike
        (6000.0, "C", 0, None, 0.5, 0.001, 0.14, 0.1, 1.0, 1.0), # no oi
        (6000.0, "C", 0, 100, 0.5, 0.001, None, 0.1, 1.0, 1.0),  # no iv
        (6000.0, "C", 0, 100, 0.5, 0.001, 0.0, 0.1, 1.0, 1.0),   # zero iv
        (6000.0, "C", 0, 0, 0.5, 0.001, 0.14, 0.1, 1.0, 1.0),    # zero oi
        (-10.0, "C", 0, 100, 0.5, 0.001, 0.14, 0.1, 1.0, 1.0),   # bad strike
    ]
    assert mb.build_book(rows, dte_max=7).n == 1


def test_build_book_tolerates_missing_optional_columns():
    """Rows banked before the 2026-10-25 widening have no gxoi and no flow."""
    book = mb.build_book([(6000.0, "C", 0, 100, 0.5, 0.001, 0.14)], dte_max=7)
    assert book.n == 1
    assert mb.dealer_gamma_dollars(book, 6000.0) > 0
    assert mb.walls(book, 6000.0)[1] == 6000.0              # re-anchored wall works
    assert mb.walls_vendor(book, 6000.0) == (None, None)    # stale version cannot
    assert mb.signed_flow(book) == {"found": False}


def test_build_book_empty():
    book = mb.build_book([], dte_max=7)
    assert book.empty
    assert mb.dealer_gamma_dollars(book, 6000.0) == 0.0
    assert mb.gamma_flip(book, 6000.0) is None
    assert mb.walls(book, 6000.0) == (None, None)
    assert mb.heaviest_strike(book, 6000.0) is None
    assert mb.hedge_pressure(book, 6000.0) == {}


# ── gamma sign + flip ──────────────────────────────────────────────────


def test_dealer_gamma_sign_convention():
    """A pure call book is long gamma (+), a pure put book short (−)."""
    calls = mb.build_book([(6000.0, "C", 7, 10_000, 0.5, 0.0, 0.15, 5.0, 1.0, 1.0)], dte_max=7)
    puts = mb.build_book([(6000.0, "P", 7, 10_000, -0.5, 0.0, 0.15, 5.0, 1.0, 1.0)], dte_max=7)
    assert mb.dealer_gamma_dollars(calls, 6000.0) > 0
    assert mb.dealer_gamma_dollars(puts, 6000.0) < 0


def test_flip_found_between_put_and_call_concentration():
    rows = [
        (5800.0, "P", 7, 80_000, -0.3, 0.0, 0.17, 40.0, 1.0, 1.0),
        (6200.0, "C", 7, 80_000, 0.3, 0.0, 0.13, 40.0, 1.0, 1.0),
    ]
    flip = mb.gamma_flip(mb.build_book(rows, dte_max=7), 6000.0, span=0.08)
    assert flip is not None and 5800.0 < flip < 6200.0


def test_flip_none_when_no_crossing():
    book = mb.build_book([(6000.0, "C", 7, 10_000, 0.5, 0.0, 0.15, 5.0, 1.0, 1.0)], dte_max=7)
    assert mb.gamma_flip(book, 6000.0, span=0.02) is None


# ── walls: the re-anchor is the point ──────────────────────────────────


def test_walls_recompute_gamma_at_the_callers_spot():
    """The wall must follow spot — a stored-gxoi ranking never could.

    Same book, two evaluation spots: the chosen strikes have to move, because
    gamma is being recomputed rather than read frozen off the snapshot.
    """
    book = mb.build_book(_chain(), dte_max=7)
    low = mb.walls(book, 5800.0)
    high = mb.walls(book, 6200.0)
    assert None not in low and None not in high
    assert low != high
    assert low[1] <= high[1]


def test_walls_sit_the_right_side_of_spot():
    pw, cw = mb.walls(mb.build_book(_chain(), dte_max=7), 6000.0)
    assert pw is not None and cw is not None
    assert pw <= 6000.0 <= cw


def test_walls_aggregate_across_expiries():
    rows = [
        (6100.0, "C", 0, 30_000, 0.3, 0.0, 0.14, 30.0, 1.0, 1.0),
        (6200.0, "C", 0, 20_000, 0.2, 0.0, 0.14, 20.0, 1.0, 1.0),
        (6200.0, "C", 7, 20_000, 0.2, 0.0, 0.14, 20.0, 1.0, 1.0),
    ]
    _, cw = mb.walls(mb.build_book(rows, dte_max=7), 6000.0)
    assert cw == 6200.0


def test_stored_gxoi_version_is_kept_only_for_comparison():
    book = mb.build_book(_chain(), dte_max=7)
    assert mb.walls_vendor(book, 6000.0) == (5900.0, 6100.0)
    assert mb.walls_vendor(mb.build_book([], dte_max=7), 6000.0) == (None, None)


def test_heaviest_strike_finds_the_biggest_pile():
    assert mb.heaviest_strike(mb.build_book(_chain(), dte_max=7), 6000.0) is not None


# ── signed flow ────────────────────────────────────────────────────────


def test_signed_flow_direction_and_tilt():
    f = mb.signed_flow(mb.build_book(_chain(), dte_max=7))
    assert f["found"] is True
    assert f["net"] < 0                 # puts 3 bought / 9 sold dominates
    assert f["customers"] == "sold"
    assert f["steadying"] is True
    assert -1.0 <= f["tilt"] <= 1.0


def test_signed_flow_reports_at_requested_strikes():
    f = mb.signed_flow(mb.build_book(_chain(), dte_max=7), strikes=[5900.0, 6100.0])
    assert {d["strike"] for d in f["at_levels"]} == {5900.0, 6100.0}
    for d in f["at_levels"]:
        assert d["customers"] in ("sold", "bought")


def test_signed_flow_ignores_missing_strikes_and_nones():
    f = mb.signed_flow(mb.build_book(_chain(), dte_max=7), strikes=[None, 1.0, 5900.0])
    assert [d["strike"] for d in f["at_levels"]] == [5900.0]


def test_signed_flow_busiest_is_ranked_by_magnitude():
    b = mb.signed_flow(mb.build_book(_chain(), dte_max=7))["busiest"]
    assert len(b) <= 3
    mags = [abs(d["net"]) for d in b]
    assert mags == sorted(mags, reverse=True)


def test_signed_flow_absent_when_columns_are_null():
    rows = [(6000.0, "C", 0, 100, 0.5, 0.001, 0.14, 1.0, None, None)]
    assert mb.signed_flow(mb.build_book(rows, dte_max=7)) == {"found": False}
    assert mb.signed_flow(mb.build_book([], dte_max=7)) == {"found": False}


def test_signed_flow_customers_buying_is_not_steadying():
    rows = [(6000.0, "C", 0, 100, 0.5, 0.001, 0.14, 1.0, 9.0, 2.0)]
    f = mb.signed_flow(mb.build_book(rows, dte_max=7))
    assert f["net"] > 0 and f["customers"] == "bought" and f["steadying"] is False


# ── cones, IV, hedge pressure ──────────────────────────────────────────


def test_atm_iv_picks_the_nearest_front_strike():
    assert mb.atm_iv(mb.build_book(_chain(), dte_max=7), 6000.0) in (0.14, 0.16)
    assert mb.atm_iv(mb.build_book([], dte_max=7), 6000.0) is None


def test_delta_cones_bracket_spot_and_widen():
    cones = mb.delta_cones(_chain(), 6000.0, expiry_dte=0)
    assert [c["band_pct"] for c in cones] == [50, 70, 80]
    widths = []
    for c in cones:
        assert c["lower"] is not None and c["upper"] is not None
        assert c["lower"] < 6000.0 < c["upper"]
        widths.append(c["upper"] - c["lower"])
    assert widths[0] < widths[1] < widths[2]


def test_delta_cones_thin_chain_returns_none():
    cones = mb.delta_cones([(6100.0, "C", 0, 10, 0.25, 0.0, 0.14, 0.1, 1.0, 1.0)], 6000.0)
    assert all(c["upper"] is None and c["lower"] is None for c in cones)


def test_hedge_pressure_short_gamma_sells_the_drop():
    rows = [(5950.0, "P", 7, 100_000, -0.4, 0.0, 0.18, 55.0, 1.0, 1.0)]
    hp = mb.hedge_pressure(mb.build_book(rows, dte_max=7), 6000.0)
    assert hp["net_gamma_dollars"] < 0
    assert hp["sell_on_drop"] > 0
    assert hp["buy_on_rally"] < 0


def test_hedge_pressure_iv_shift_changes_the_number():
    hp = mb.hedge_pressure(mb.build_book(_chain(), dte_max=7), 6000.0, iv_up=2.0, iv_down=2.0)
    assert hp["sell_on_drop_iv"] != hp["sell_on_drop"]
    assert hp["buy_on_rally_iv"] != hp["buy_on_rally"]
    assert hp["ratio"] is not None and hp["ratio"] > 0


def test_iv_shift_moves_the_book_for_the_how_solid_check():
    book = mb.build_book(_chain(), dte_max=7)
    base = mb.dealer_gamma_dollars(book, 6000.0)
    assert mb.dealer_gamma_dollars(book, 6000.0, iv_shift=1.0) != base
    assert mb.dealer_gamma_dollars(book, 6000.0, iv_shift=-1.0) != base
    assert mb.walls(book, 6000.0, iv_shift=1.0)[1] is not None
