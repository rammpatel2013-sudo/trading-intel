"""Phase 5 B1 — beta-reset states (pure)."""

from datetime import date, timedelta

from trading_intel.market import beta_reset as br


def _mk(n, f):
    d0 = date(2024, 1, 1)
    return {d0 + timedelta(days=i): f(i) for i in range(n)}


def test_quiet_market_is_intact():
    spx = _mk(400, lambda i: 4000 * (1 + 0.0004) ** i * (1 + 0.004 * ((-1) ** i)))
    splv = _mk(400, lambda i: 60 * (1 + 0.0002) ** i * (1 + 0.001 * ((-1) ** i)))
    sphb = _mk(400, lambda i: 80 * (1 + 0.0006) ** i * (1 + 0.007 * ((-1) ** i)))
    rows = br.compute(spx, splv, sphb)
    assert rows[-1].low_beta_state == "intact"
    assert rows[-1].beta_frozen is not None
    assert "Low beta" in br.plain_read(rows[-1])


def test_washout_breaks_low_beta_and_high_beta_holds():
    n, k = 395, 380

    def spx_f(i):
        base = 4000 * (1 + 0.0004) ** min(i, k) * (1 + 0.004 * ((-1) ** i))
        return base * (0.985 ** (i - k)) if i > k else base

    def splv_f(i):  # low beta outperforms into k (ratio highs), then falls WITH the market
        base = 60 * (1 + 0.0007) ** min(i, k) * (1 + 0.001 * ((-1) ** i))
        return base * (0.982 ** (i - k)) if i > k else base

    def sphb_f(i):  # high beta beta~1.75 pre-shock, then holds (falls far less than beta implies)
        base = 80 * (1 + 0.0007) ** min(i, k) * (1 + 0.007 * ((-1) ** i))
        return base * (0.99 ** (i - k)) if i > k else base

    cor = {"COR3M": _mk(n, lambda i: 12.0 if i <= k else 12.0 + 2.0 * (i - k))}
    rows = br.compute(_mk(n, spx_f), _mk(n, splv_f), _mk(n, sphb_f), cor)
    last = rows[-1]
    assert last.abs_break and last.rel_break and last.corr_break
    assert last.low_beta_state == "broken"
    assert last.high_beta_state == "holding"
    assert last.label == "RESET"
