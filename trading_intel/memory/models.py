"""SQLAlchemy ORM models for trading-intel.

This is the source of truth for the database schema. Every table here
must be created via an Alembic migration — never directly.

When adding a new model:
1. Add the class here
2. Generate a migration: `alembic revision --autogenerate -m "add foo table"`
3. Review and edit the generated migration
4. Apply: `alembic upgrade head`
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Root of the ORM hierarchy."""


# ── Tickers and market data ────────────────────────────────────────────


class Ticker(Base):
    __tablename__ = "tickers"

    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255))
    sector: Mapped[str | None] = mapped_column(String(64))
    industry: Mapped[str | None] = mapped_column(String(64))
    gics_id: Mapped[str | None] = mapped_column(String(16))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class QuoteDaily(Base):
    __tablename__ = "quotes_daily"
    __table_args__ = (UniqueConstraint("symbol", "date", name="uq_quotes_daily"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), ForeignKey("tickers.symbol"))
    date: Mapped[date] = mapped_column(Date)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[int] = mapped_column(BigInteger)  # index/aggregate volume can exceed int4
    rv20: Mapped[float | None] = mapped_column(Float)
    rv60: Mapped[float | None] = mapped_column(Float)


# ── Greeks ─────────────────────────────────────────────────────────────


class GreeksSnapshot(Base):
    """Aggregate per-ticker GEX/DEX/VEX/CHEX time series."""

    __tablename__ = "greeks_snapshots"
    __table_args__ = (UniqueConstraint("symbol", "ts", "source", name="uq_greeks_snap"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16))
    ts: Mapped[datetime] = mapped_column(DateTime)
    spot: Mapped[float | None] = mapped_column(Float)
    gex_total: Mapped[float | None] = mapped_column(Float)
    dex_total: Mapped[float | None] = mapped_column(Float)
    vex_total: Mapped[float | None] = mapped_column(Float)
    chex_total: Mapped[float | None] = mapped_column(Float)
    gex_flip: Mapped[float | None] = mapped_column(Float)
    gex_rvol_ratio: Mapped[float | None] = mapped_column(Float)
    atm_iv: Mapped[float | None] = mapped_column(Float)
    dex_flip: Mapped[float | None] = mapped_column(Float)
    call_volume: Mapped[float | None] = mapped_column(Float)
    put_volume: Mapped[float | None] = mapped_column(Float)
    call_notional: Mapped[float | None] = mapped_column(Float)
    put_notional: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32), default="convex")  # convex, schwab_legacy, etc.


class GreeksChain(Base):
    """Per-strike snapshot. Heavier — 1/day + key intraday strikes."""

    __tablename__ = "greeks_chain"
    __table_args__ = (
        UniqueConstraint(
            "symbol", "ts", "source", "expiry", "strike", "cp", name="uq_greeks_chain"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16))
    ts: Mapped[datetime] = mapped_column(DateTime)
    expiry: Mapped[date] = mapped_column(Date)
    strike: Mapped[float] = mapped_column(Float)
    cp: Mapped[str] = mapped_column(String(1))  # 'C' or 'P'
    oi: Mapped[int | None] = mapped_column(Integer)
    volume: Mapped[int | None] = mapped_column(Integer)
    delta: Mapped[float | None] = mapped_column(Float)
    gamma: Mapped[float | None] = mapped_column(Float)
    theta: Mapped[float | None] = mapped_column(Float)
    vega: Mapped[float | None] = mapped_column(Float)
    vanna: Mapped[float | None] = mapped_column(Float)
    charm: Mapped[float | None] = mapped_column(Float)
    iv: Mapped[float | None] = mapped_column(Float)
    gxoi: Mapped[float | None] = mapped_column(Float)
    dxoi: Mapped[float | None] = mapped_column(Float)
    vxoi: Mapped[float | None] = mapped_column(Float)
    cxoi: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32), default="convex")


class OiChainEod(Base):
    """End-of-day wide (~180d) per-strike chain for the OI/flow change study.

    One row per (symbol, ts[day], expiry, strike, cp). Carries open interest,
    the vendor's day-over-day OI change (Convex ``oi_ch`` -> ``oi_change``),
    traded volume, signed greek-OI exposures and the raw greeks/iv. Day-over-day
    diffs (volume vs ΔOI vs ΔGEX) are computed downstream — regime descriptors,
    not signals (FlashAlpha rule 4).
    """

    __tablename__ = "oi_chain_eod"
    __table_args__ = (
        UniqueConstraint(
            "symbol", "ts", "source", "expiry", "strike", "cp", name="uq_oi_chain_eod"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16))
    ts: Mapped[datetime] = mapped_column(DateTime)
    expiry: Mapped[date] = mapped_column(Date)
    strike: Mapped[float] = mapped_column(Float)
    cp: Mapped[str] = mapped_column(String(1))  # 'C' or 'P'
    dte: Mapped[int | None] = mapped_column(Integer)
    oi: Mapped[int | None] = mapped_column(Integer)
    oi_change: Mapped[int | None] = mapped_column(Integer)
    volume: Mapped[int | None] = mapped_column(Integer)
    delta: Mapped[float | None] = mapped_column(Float)
    gamma: Mapped[float | None] = mapped_column(Float)
    iv: Mapped[float | None] = mapped_column(Float)
    gxoi: Mapped[float | None] = mapped_column(Float)
    dxoi: Mapped[float | None] = mapped_column(Float)
    vxoi: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32), default="convex")


class FlowBucket(Base):
    """Convex time-bucketed flow data."""

    __tablename__ = "flow_buckets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16))
    ts: Mapped[datetime] = mapped_column(DateTime)
    bucket_min: Mapped[int] = mapped_column(Integer)  # 5, 15, 30
    volm: Mapped[float | None] = mapped_column(Float)
    value: Mapped[float | None] = mapped_column(Float)
    volmbs: Mapped[float | None] = mapped_column(Float)
    valuebs: Mapped[float | None] = mapped_column(Float)
    flowratio: Mapped[float | None] = mapped_column(Float)


class GexRolling(Base):
    """Long-dated (rolling) total GEX per symbol — EOD cadence.

    ``gex_total`` is net signed gxoi (calls +, puts -) summed across every
    expiration within ``window_days`` (default ~180 / 6 months). Tracks
    directional positioning drift over time. Paired with per-expiration detail
    in ``gex_term``.
    """

    __tablename__ = "gex_rolling"
    __table_args__ = (UniqueConstraint("symbol", "ts", "source", name="uq_gex_rolling"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16))
    ts: Mapped[datetime] = mapped_column(DateTime)
    spot: Mapped[float | None] = mapped_column(Float)
    window_days: Mapped[int] = mapped_column(Integer)
    gex_total: Mapped[float | None] = mapped_column(Float)  # net signed gxoi over window
    n_expirations: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(32), default="convex")


class GexTerm(Base):
    """Per-expiration net gxoi (term structure) for a rolling snapshot.

    One row per expiration; ties back to a ``gex_rolling`` row by the natural
    key (symbol, ts, source).
    """

    __tablename__ = "gex_term"
    __table_args__ = (UniqueConstraint("symbol", "ts", "source", "expiration", name="uq_gex_term"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16))
    ts: Mapped[datetime] = mapped_column(DateTime)
    expiration: Mapped[date] = mapped_column(Date)
    dte: Mapped[int | None] = mapped_column(Integer)
    gex: Mapped[float | None] = mapped_column(Float)  # net signed gxoi for this expiration
    source: Mapped[str] = mapped_column(String(32), default="convex")


# ── VIX / macro ────────────────────────────────────────────────────────


class VixData(Base):
    __tablename__ = "vix_data"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    vix: Mapped[float | None] = mapped_column(Float)
    vvix: Mapped[float | None] = mapped_column(Float)
    move: Mapped[float | None] = mapped_column(Float)
    hy_oas: Mapped[float | None] = mapped_column(Float)
    ig_oas: Mapped[float | None] = mapped_column(Float)
    vix_sd20: Mapped[float | None] = mapped_column(Float)
    vvix_sd20: Mapped[float | None] = mapped_column(Float)
    vega_zone: Mapped[str | None] = mapped_column(String(16))  # low/mid/high
    # CBOE index term structure (persisted from vix_snapshot) + variance risk premium
    vix9d: Mapped[float | None] = mapped_column(Float)
    vix3m: Mapped[float | None] = mapped_column(Float)
    vix6m: Mapped[float | None] = mapped_column(Float)
    vrp: Mapped[float | None] = mapped_column(Float)  # VIX - SPX 20d realized vol (vol pts)


class VixExpiration(Base):
    """Standard (monthly) VIX expiration calendar — one row per settlement date.

    Fully deterministic (Cboe spec: the Wednesday 30 days before the following
    month's third-Friday SPX expiration, rolled back on holidays). Computed and
    upserted by ``scheduler/jobs/vix_expirations.py`` from ``vol.vix_calendar``;
    no vendor call. ``spx_ref_expiry`` is the paired SPX third Friday and
    ``holiday_adjusted`` flags rows that rolled off the normal Wednesday.
    Descriptive calendar data, not a signal (FlashAlpha rule 4).
    """

    __tablename__ = "vix_expirations"

    expiration: Mapped[date] = mapped_column(Date, primary_key=True)
    spx_ref_expiry: Mapped[date] = mapped_column(Date)
    holiday_adjusted: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[date] = mapped_column(Date)


class VolRichness(Base):
    """Daily vol-richness scan row per (symbol, trading-day, horizon).

    IV-vs-forward-RV variance-risk-premium standardized to the name's own
    trailing history (``vrp_pctile`` / ``iv_rank``), plus the term/skew context
    and the VEGA/VIX regime-gated descriptive ``label``. Populated EOD by
    ``scheduler/jobs/vol_richness.py`` from STORED data only.

    **UN-PRUNED on purpose:** this is the long IV/VRP percentile baseline the
    standardization reads back (``oi_chain_eod`` prunes at 90d, so it can't serve
    that role). Descriptor only — never a signal (FlashAlpha rule 4).
    """

    __tablename__ = "vol_richness"
    __table_args__ = (
        UniqueConstraint("symbol", "ts", "horizon_dte", name="uq_vol_richness"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), ForeignKey("tickers.symbol"))
    ts: Mapped[date] = mapped_column(Date)  # trading day
    horizon_dte: Mapped[int] = mapped_column(Integer)  # 30 / 60
    iv_atm: Mapped[float | None] = mapped_column(Float)
    fcst_rv: Mapped[float | None] = mapped_column(Float)
    vrp_pts: Mapped[float | None] = mapped_column(Float)
    vrp_pctile: Mapped[float | None] = mapped_column(Float)
    iv_rank: Mapped[float | None] = mapped_column(Float)
    term_slope: Mapped[float | None] = mapped_column(Float)
    skew_25d: Mapped[float | None] = mapped_column(Float)
    regime_zone: Mapped[str | None] = mapped_column(String(16))  # low/mid/high
    richness_score: Mapped[float | None] = mapped_column(Float)
    label: Mapped[str | None] = mapped_column(String(64))


class SkewSnapshot(Base):
    """Daily per-name volatility-skew descriptor row per (symbol, day, horizon).

    Captures the FX-convention surface coordinates at 10D and 25D - risk
    reversals (``iv_put_d - iv_call_d``) and butterflies (``avg(wing) - atm``) -
    along with their trailing-window percentiles (63d / 252d), the front-vs-back
    skew slope, the name's 60d VIX beta, the abnormal RR (the residual of
    ``Drr_25d`` after removing what the name's VIX-beta predicts from ``DSDEX``),
    a shift-vs-slide label decomposing the day's surface move, and a descriptive
    summary ``label``. Populated EOD by ``scheduler/jobs/skew_snapshots.py`` from
    STORED data only.

    **UN-PRUNED on purpose:** this is the long skew percentile baseline the
    standardization reads back (``oi_chain_eod`` prunes at 90d, so it can't serve
    that role). Per ADR-003 (revision 2), skew is signal-eligible - but rule 4's
    architectural constraint stands: only ``strategies/skew.py`` writes to
    ``signals``; this descriptor table stays under ``vol/``.
    """

    __tablename__ = "skew_snapshots"
    __table_args__ = (
        UniqueConstraint("symbol", "ts", "horizon_dte", name="uq_skew_snapshots"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), ForeignKey("tickers.symbol"))
    ts: Mapped[date] = mapped_column(Date)  # trading day
    horizon_dte: Mapped[int] = mapped_column(Integer)  # 30 / 60 / 90 / 180 / 365
    atm_iv: Mapped[float | None] = mapped_column(Float)
    rr_10d: Mapped[float | None] = mapped_column(Float)
    rr_25d: Mapped[float | None] = mapped_column(Float)
    bf_10d: Mapped[float | None] = mapped_column(Float)
    bf_25d: Mapped[float | None] = mapped_column(Float)
    rr_25d_pctile_63d: Mapped[float | None] = mapped_column(Float)
    rr_25d_pctile_252d: Mapped[float | None] = mapped_column(Float)
    bf_25d_pctile_252d: Mapped[float | None] = mapped_column(Float)
    front_back_rr_slope: Mapped[float | None] = mapped_column(Float)
    vix_beta_60d: Mapped[float | None] = mapped_column(Float)
    rr_25d_abnormal: Mapped[float | None] = mapped_column(Float)
    shift_slide_label: Mapped[str | None] = mapped_column(String(16))
    label: Mapped[str | None] = mapped_column(String(64))


class IvTenorSnapshot(Base):
    """Daily constant-maturity forward-IV row per (symbol, day, tenor).

    The index-ETF complement to ``skew_snapshots``: SPY / QQQ / SPX are kept
    OUT of the per-strike persisters (``CHAIN_EXCLUDE_ROOTS``), so the delta-
    surface machinery that feeds ``skew_snapshots`` has no stored chain for
    them. This table is populated EOD by ``scheduler/jobs/iv_tenor_snapshots.py``
    from a LIVE Convex chain pull (no per-strike rows persisted) — the surface is
    built in memory and only this small aggregate row is written.

    Each row is one *constant-maturity* tenor (``tenor_dte`` = 30 / 90 days),
    interpolated in total-variance space across the listed expiries so the
    historical line never sawtooths as expiries roll. Wings are stored per side
    in the equity sign convention: ``iv_put_*`` is the downside (-Δ) wing,
    ``iv_call_*`` the upside (+Δ) wing; 50Δ ≡ ATM is ``iv_atm``. A 25Δ risk
    reversal is therefore ``iv_put_25d - iv_call_25d`` at read time.

    Regime descriptor only (FlashAlpha rule 4) — emits no signals.
    """

    __tablename__ = "iv_tenor_snapshots"
    __table_args__ = (
        UniqueConstraint("symbol", "ts", "tenor_dte", name="uq_iv_tenor_snapshots"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), ForeignKey("tickers.symbol"))
    ts: Mapped[date] = mapped_column(Date)  # trading day
    tenor_dte: Mapped[int] = mapped_column(Integer)  # constant maturity: 30 / 90
    iv_atm: Mapped[float | None] = mapped_column(Float)  # 50Δ ATM IV (decimal)
    iv_call_15d: Mapped[float | None] = mapped_column(Float)  # +15Δ upside wing
    iv_put_15d: Mapped[float | None] = mapped_column(Float)  # -15Δ downside wing
    iv_call_25d: Mapped[float | None] = mapped_column(Float)  # +25Δ upside wing
    iv_put_25d: Mapped[float | None] = mapped_column(Float)  # -25Δ downside wing
    spot: Mapped[float | None] = mapped_column(Float)  # underlying at capture
    n_expiries: Mapped[int | None] = mapped_column(Integer)  # expiries in the interp


class LetfSharesSnapshot(Base):
    """Daily shares-outstanding snapshot per (symbol, trading day) for LETFs.

    The primitive behind LETF net creation/redemption (issuance) flow. FMP's
    stable tier serves only the CURRENT shares figure, so ``scheduler/jobs/
    letf_flows.py`` snapshots it EOD and banks the series forward; Δshares, net
    issuance $ (= Δshares × price), issuer buckets, and the k(k-1)·assets·return
    forced-rebalance estimate are all computed downstream from this raw series.

    ``nav`` is the fund NAV/close at capture (joined from the price layer) so the
    $-flow descriptor is self-contained; ``vendor_date`` is the vendor's stated
    as-of for the shares figure (may lag ``ts``). Unique on (symbol, ts) for
    idempotent daily upserts (rule 5). Regime descriptor only (rule 4) — no signals.
    """

    __tablename__ = "letf_shares_snapshots"
    __table_args__ = (
        UniqueConstraint("symbol", "ts", name="uq_letf_shares_snapshots"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), ForeignKey("tickers.symbol"))
    ts: Mapped[date] = mapped_column(Date)  # our snapshot trading day
    shares_outstanding: Mapped[int] = mapped_column(BigInteger)  # whole shares
    float_shares: Mapped[int | None] = mapped_column(BigInteger)  # free float, if given
    nav: Mapped[float | None] = mapped_column(Float)  # fund NAV/close at capture
    vendor_date: Mapped[date | None] = mapped_column(Date)  # vendor as-of for shares
    source: Mapped[str | None] = mapped_column(String(32))  # vendor tag (shares-float/quote)


class IndexSkewDaily(Base):
    """Index-level skew snapshot per trading day.

    Stores Cboe SKEW (third-moment estimator), Nations SkewDex (``SDEX`` -
    ATM-vs-1-sigma-OTM-put SPY skew), our own SPX 25-delta RR and its trailing
    percentile, mirrored VVIX, and the VIX-options-derived tail-hedging
    composite. Populated EOD by ``scheduler/jobs/index_skew.py``. Per ADR-003
    sections 2.3 and 3.4.
    """

    __tablename__ = "index_skew_daily"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    cboe_skew: Mapped[float | None] = mapped_column(Float)
    sdex: Mapped[float | None] = mapped_column(Float)
    spx_rr_25d_30d: Mapped[float | None] = mapped_column(Float)
    spx_rr_pctile_252d: Mapped[float | None] = mapped_column(Float)
    sdex_pctile_252d: Mapped[float | None] = mapped_column(Float)
    vvix: Mapped[float | None] = mapped_column(Float)
    vix_call_skew_25d: Mapped[float | None] = mapped_column(Float)
    vix_call_oi_share: Mapped[float | None] = mapped_column(Float)
    vix_tail_hedging_score: Mapped[float | None] = mapped_column(Float)
    # Nations Indexes family — added in migration 0022.
    # ``voli`` / ``tdex`` are Yahoo-sourced (^VOLI, ^TDEX).
    # ``*_proxy`` are computed from the SPX delta surface — Nations does not
    # publish CallDex/PutDex/RiskDex on Yahoo (subscription only); the proxies
    # use IV at 15Δ (≈1-sigma OTM) @ 30d, which carries the same regime info.
    voli: Mapped[float | None] = mapped_column(Float)
    voli_pctile_252d: Mapped[float | None] = mapped_column(Float)
    tdex: Mapped[float | None] = mapped_column(Float)
    tdex_pctile_252d: Mapped[float | None] = mapped_column(Float)
    calldex_proxy: Mapped[float | None] = mapped_column(Float)
    calldex_proxy_pctile_252d: Mapped[float | None] = mapped_column(Float)
    putdex_proxy: Mapped[float | None] = mapped_column(Float)
    putdex_proxy_pctile_252d: Mapped[float | None] = mapped_column(Float)
    riskdex_proxy: Mapped[float | None] = mapped_column(Float)
    riskdex_proxy_pctile_252d: Mapped[float | None] = mapped_column(Float)
    # VIX decomposition family — migration 0023. Term-structure raw tenors +
    # the five derived dimension descriptors the regime classifier reads.
    vix9d: Mapped[float | None] = mapped_column(Float)
    vix3m: Mapped[float | None] = mapped_column(Float)
    vix6m: Mapped[float | None] = mapped_column(Float)
    vix_voli_spread: Mapped[float | None] = mapped_column(Float)
    vix_term_9d_30d: Mapped[float | None] = mapped_column(Float)
    vix_term_3m_30d: Mapped[float | None] = mapped_column(Float)
    vix_spx_beta_60d: Mapped[float | None] = mapped_column(Float)
    vvix_vix_ratio: Mapped[float | None] = mapped_column(Float)
    vix_options_richness: Mapped[float | None] = mapped_column(Float)
    # Cboe implied-correlation / dispersion family — migration 0025.
    # ``cor1m`` / ``cor3m`` are Yahoo-sourced (^COR1M, ^COR3M). High = correlation
    # (index-vol-led) regime; low = dispersion. ``cor1m - cor3m`` is the
    # correlation-curve slope (positive = near-term correlation stress).
    cor1m: Mapped[float | None] = mapped_column(Float)
    cor1m_pctile_252d: Mapped[float | None] = mapped_column(Float)
    cor3m: Mapped[float | None] = mapped_column(Float)
    cor3m_pctile_252d: Mapped[float | None] = mapped_column(Float)
    # Cboe constituent-vol / dispersion family — migration 0027.
    # ``vixeq`` (^VIXEQ) is single-stock vol; ``dspx`` (^DSPX) the official
    # dispersion index (DSPX^2 = VIXEQ^2 - VIX^2). ``vixeq_vix_spread`` =
    # VIXEQ - VIX (wide = high dispersion / low correlation).
    vixeq: Mapped[float | None] = mapped_column(Float)
    vixeq_pctile_252d: Mapped[float | None] = mapped_column(Float)
    dspx: Mapped[float | None] = mapped_column(Float)
    dspx_pctile_252d: Mapped[float | None] = mapped_column(Float)
    vixeq_vix_spread: Mapped[float | None] = mapped_column(Float)


class VixOptionsChain(Base):
    """EOD snapshot of one VIX options chain row (per ts/expiry/strike/kind).

    Pulled via ``OptionsDataSource.vix_chain`` by
    ``scheduler/jobs/vix_options.py``. The dashboard reads this for the
    VIX-options view; the EOD index-skew job aggregates it into
    ``index_skew_daily``.
    """

    __tablename__ = "vix_options_chain"
    __table_args__ = (
        UniqueConstraint(
            "ts", "expiration", "strike", "opt_kind", name="uq_vix_options_chain"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[date] = mapped_column(Date)
    expiration: Mapped[date] = mapped_column(Date)
    strike: Mapped[float] = mapped_column(Float)
    opt_kind: Mapped[str] = mapped_column(String(4))  # call / put
    delta: Mapped[float | None] = mapped_column(Float)
    iv: Mapped[float | None] = mapped_column(Float)
    oi: Mapped[float | None] = mapped_column(Float)
    oi_change: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)


class DeltaFlow(Base):
    """Intraday cumulative traded delta-notional per symbol/snapshot.

    Five-minute snapshots of the running dollar-delta of the day's option flow,
    split call vs put and ALL expiries vs the NEXT (nearest) expiry. Powers the
    delta-notional flow chart (price overlaid with cumulative call/put delta).
    Written by ``scheduler/jobs/delta_flow.py``. Descriptor only (rule 4).
    """

    __tablename__ = "delta_flow"
    __table_args__ = (
        UniqueConstraint("symbol", "ts", "source", name="uq_delta_flow"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16))
    ts: Mapped[datetime] = mapped_column(DateTime)  # 5-min slot
    spot: Mapped[float | None] = mapped_column(Float)
    next_expiry: Mapped[date | None] = mapped_column(Date)
    call_notional_all: Mapped[float | None] = mapped_column(Float)
    put_notional_all: Mapped[float | None] = mapped_column(Float)
    call_notional_next: Mapped[float | None] = mapped_column(Float)
    put_notional_next: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32), default="convex")


class LiveGex(Base):
    """Intraday (live) per-strike GEX snapshot — delta-band, pruned at EOD.

    Refreshed every few minutes during RTH for the live GEX view, filtered to the
    near-the-money delta band (|delta| ~0.30-0.70). Rows are pruned at end of day
    (the daily ``greeks_chain`` / ``greeks_snapshots`` stay for historical trend).
    Descriptor only — not a signal (FlashAlpha rule 4).
    """

    __tablename__ = "live_gex"
    __table_args__ = (
        UniqueConstraint("symbol", "ts", "strike", "cp", "expiry", name="uq_live_gex"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16))
    ts: Mapped[datetime] = mapped_column(DateTime)
    strike: Mapped[float] = mapped_column(Float)
    cp: Mapped[str] = mapped_column(String(1))  # 'C' or 'P'
    expiry: Mapped[date | None] = mapped_column(Date)  # per-expiry decomposition
    spot: Mapped[float | None] = mapped_column(Float)
    delta: Mapped[float | None] = mapped_column(Float)
    gamma: Mapped[float | None] = mapped_column(Float)
    iv: Mapped[float | None] = mapped_column(Float)
    gxoi: Mapped[float | None] = mapped_column(Float)
    dxoi: Mapped[float | None] = mapped_column(Float)
    oi: Mapped[float | None] = mapped_column(Float)  # open interest (for charm/vanna exposure)
    vanna: Mapped[float | None] = mapped_column(Float)  # raw greek; exposure = vanna * oi
    charm: Mapped[float | None] = mapped_column(Float)  # raw greek; exposure = charm * oi
    # Today's signed flow from ConvexValue ``flowsum`` (migration 0018). Nullable
    # — until the NAS image is rebuilt to pull flow, these stay NULL and the
    # gamma-profile / force-attribution consumers fall back to OI-only.
    volm_buy: Mapped[float | None] = mapped_column(Float)
    volm_sell: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32), default="convex")


# ── Earnings ───────────────────────────────────────────────────────────


class EarningsEvent(Base):
    __tablename__ = "earnings_events"
    # (symbol, date) unique so the earn_cal collector can upsert idempotently
    # (CLAUDE.md rule 5). Added by migration 0037; the table itself is 0001.
    __table_args__ = (UniqueConstraint("symbol", "date", name="uq_earnings_events"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16))
    date: Mapped[date] = mapped_column(Date)
    time: Mapped[str | None] = mapped_column(String(8))  # 'BMO' or 'AMC'
    actual: Mapped[float | None] = mapped_column(Float)
    estimate: Mapped[float | None] = mapped_column(Float)
    surprise_pct: Mapped[float | None] = mapped_column(Float)
    read_through_class: Mapped[str | None] = mapped_column(String(32))
    peer_impacts: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class PreEarningsStraddle(Base):
    """The implied expected-move BASELINE captured just before an earnings print.

    The EM-break detector (``earnings/em_break.py``) needs the options-implied
    range as it stood pre-earnings to measure how far the realized gap broke it.
    The ``pre_earnings_straddle`` collector snapshots the ~30-DTE (earnings-
    bracketing) ATM straddle for each name with an upcoming ``earnings_events``
    date and upserts one row per (symbol, earnings_date) — so the row always holds
    the freshest pre-print read. ``em_pct`` = ``straddle / spot``.

    Regime descriptor input only (FlashAlpha rule 4).
    """

    __tablename__ = "pre_earnings_straddle"
    __table_args__ = (
        UniqueConstraint("symbol", "earnings_date", name="uq_pre_earnings_straddle"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16))
    earnings_date: Mapped[date] = mapped_column(Date)
    snap_ts: Mapped[datetime] = mapped_column(DateTime)
    dte: Mapped[int | None] = mapped_column(Integer)
    straddle: Mapped[float | None] = mapped_column(Float)
    em_pct: Mapped[float | None] = mapped_column(Float)  # straddle / spot
    atm_iv: Mapped[float | None] = mapped_column(Float)
    spot: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32), default="convex")


# ── Macro themes (Layer 1: pgvector) ──────────────────────────────────


class Theme(Base):
    __tablename__ = "themes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    scope: Mapped[str] = mapped_column(Enum("macro", "sector", "company", name="theme_scope"))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("themes.id"))


class ThemeObservation(Base):
    __tablename__ = "theme_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    theme_id: Mapped[int] = mapped_column(ForeignKey("themes.id"))
    symbol: Mapped[str | None] = mapped_column(String(16))
    date: Mapped[date] = mapped_column(Date)
    sentiment: Mapped[float | None] = mapped_column(Float)  # -1.0 to 1.0
    source_doc_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"))
    quote_text: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    path: Mapped[str] = mapped_column(String(512))
    source: Mapped[str | None] = mapped_column(String(32))  # internal, broker, sec, etc.
    type: Mapped[str | None] = mapped_column(String(32))  # pdf, transcript, etc.
    kind: Mapped[str] = mapped_column(
        String(16), server_default="methodology", default="methodology", nullable=False
    )  # methodology (LLM reasoning) | research (company/theme RAG)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    page_count: Mapped[int | None] = mapped_column(Integer)


# Note: Chunk model with pgvector requires the pgvector extension to be
# installed in Postgres. The Vector column type is imported lazily.
class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"))
    chunk_idx: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    # embedding column added via migration using pgvector's Vector type
    theme_ids: Mapped[list[int] | None] = mapped_column(ARRAY(Integer))
    symbols: Mapped[list[str] | None] = mapped_column(ARRAY(String(16)))
    date: Mapped[date | None] = mapped_column(Date)


# ── Signals + alerts ───────────────────────────────────────────────────


class Signal(Base):
    """All triggered signals. Only `strategies/*` modules write to this table."""

    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime)
    symbol: Mapped[str] = mapped_column(String(16))
    signal_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    confidence: Mapped[float | None] = mapped_column(Float)


class AlertSent(Base):
    __tablename__ = "alerts_sent"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"))
    channel: Mapped[str] = mapped_column(String(32))
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    response_code: Mapped[int | None] = mapped_column(Integer)


class SignalOutcome(Base):
    """Realized outcome of a signal — the P6 backtest ledger (bank-forward).

    The ``em_break_validation`` job walks ``quotes_daily`` forward from each banked
    ``EM_BREAK_REENTRY`` signal and records whether the call-wall target or the
    put-wall stop was hit first, with the R-multiple. Read-only analytics on top of
    ``signals`` (never mutates it). One row per signal (unique ``signal_id``); an
    OPEN trade is re-evaluated and overwritten until it closes. See
    ``docs/em_break_backtest.md``.
    """

    __tablename__ = "signal_outcomes"
    __table_args__ = (UniqueConstraint("signal_id", name="uq_signal_outcomes_signal"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"))
    symbol: Mapped[str] = mapped_column(String(16))
    signal_type: Mapped[str] = mapped_column(String(64))
    entry_date: Mapped[date] = mapped_column(Date)
    entry: Mapped[float | None] = mapped_column(Float)
    target: Mapped[float | None] = mapped_column(Float)
    stop: Mapped[float | None] = mapped_column(Float)
    result: Mapped[str | None] = mapped_column(String(8))  # win / loss / open
    exit_date: Mapped[date | None] = mapped_column(Date)
    exit_price: Mapped[float | None] = mapped_column(Float)
    days_held: Mapped[int | None] = mapped_column(Integer)
    r_multiple: Mapped[float | None] = mapped_column(Float)
    conviction: Mapped[float | None] = mapped_column(Float)
    max_days: Mapped[int | None] = mapped_column(Integer)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ── AM summaries ──────────────────────────────────────────────────────


class AmSummary(Base):
    __tablename__ = "am_summaries"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    markdown: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON)
    claude_model: Mapped[str | None] = mapped_column(String(64))
    tokens_used: Mapped[int | None] = mapped_column(Integer)


# ── Intraday 0DTE/1DTE volume-weighted flow ────────────────────────────


class IntradayFlow(Base):
    """Per-strike intraday 0DTE/1DTE volume-weighted exposures.

    Populated by the 5-minute ``intraday_flow`` collector for a focused symbol
    set (SPX/SPY/QQQ) at a tight strike range. Each row carries the raw greeks
    and traded volume plus the volume-weighted gamma/delta/vanna/charm — both
    on cumulative day volume (``*_vol``) and on the per-cycle increment
    (``*_vol_iv``). The aggregate intraday time series is derived by summing the
    ``*_vol`` columns per ``ts``; the per-strike bars use the latest ``ts``.

    Regime descriptor only (FlashAlpha rule 4) — no signals.
    """

    __tablename__ = "intraday_flow"
    __table_args__ = (
        UniqueConstraint(
            "symbol", "ts", "source", "expiry", "strike", "cp", name="uq_intraday_flow"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16))
    ts: Mapped[datetime] = mapped_column(DateTime)
    source: Mapped[str] = mapped_column(String(32), default="convex")
    expiry: Mapped[date] = mapped_column(Date)
    dte: Mapped[int | None] = mapped_column(Integer)
    strike: Mapped[float] = mapped_column(Float)
    cp: Mapped[str] = mapped_column(String(1))  # 'C' or 'P'
    spot: Mapped[float | None] = mapped_column(Float)
    iv: Mapped[float | None] = mapped_column(Float)
    gamma: Mapped[float | None] = mapped_column(Float)
    delta: Mapped[float | None] = mapped_column(Float)
    vanna: Mapped[float | None] = mapped_column(Float)
    charm: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[int | None] = mapped_column(Integer)  # cumulative day volume
    volume_interval: Mapped[int | None] = mapped_column(Integer)  # fresh vs prior cycle
    # Cumulative-volume-weighted exposures.
    gamma_vol: Mapped[float | None] = mapped_column(Float)
    delta_vol: Mapped[float | None] = mapped_column(Float)
    vanna_vol: Mapped[float | None] = mapped_column(Float)
    charm_vol: Mapped[float | None] = mapped_column(Float)
    # Interval-volume-weighted exposures.
    gamma_vol_iv: Mapped[float | None] = mapped_column(Float)
    delta_vol_iv: Mapped[float | None] = mapped_column(Float)
    vanna_vol_iv: Mapped[float | None] = mapped_column(Float)
    charm_vol_iv: Mapped[float | None] = mapped_column(Float)


# ── Options flow snapshots ─────────────────────────────────────────────


class FlowSnapshot(Base):
    """Aggregate options-flow snapshot per symbol/timestamp.

    Populated by the ``flow_snapshot`` collector from ConvexValue flow + time &
    sales: call/put premium notional, the put/call tilt, net premium, the
    largest prints (``top_prints`` JSON) and notable multi-leg packages
    (``packages`` JSON). Powers the Flow dashboard page.

    Regime descriptor only (FlashAlpha rule 4) — never a signal.
    """

    __tablename__ = "flow_snapshots"
    __table_args__ = (UniqueConstraint("symbol", "ts", "source", name="uq_flow_snapshots"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16))
    ts: Mapped[datetime] = mapped_column(DateTime)
    source: Mapped[str] = mapped_column(String(32), default="convex")
    call_notional: Mapped[float | None] = mapped_column(Float)
    put_notional: Mapped[float | None] = mapped_column(Float)
    net_premium: Mapped[float | None] = mapped_column(Float)
    put_call_ratio: Mapped[float | None] = mapped_column(Float)
    tilt: Mapped[str | None] = mapped_column(String(32))
    n_prints: Mapped[int | None] = mapped_column(Integer)
    top_prints: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    packages: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)


# ── Options time & sales (Phase 3 — NAS tape capture) ──────────────────


class TasPrint(Base):
    """One large option print captured from the market-wide time-and-sales tape.

    The ``tas`` feed is live-only and market-wide; the NAS ``tas_capture_job``
    polls it during RTH, keeps prints with ``notional >= TAS_MIN_PREMIUM`` and
    upserts them here (one decoded row per print). ``symbol`` is the raw Convex
    contract (``.NVDA260619C230``); ``root``/``expiry``/``strike``/``cp`` are the
    decoded fields. Raw prints are pruned at ``TAS_RETENTION_DAYS`` (the small
    per-day roll-up is kept long-term). Descriptive flow only — never a signal
    (FlashAlpha rule 4). Idempotent via the natural print key.
    """

    __tablename__ = "tas_prints"
    __table_args__ = (
        UniqueConstraint(
            "ts", "symbol", "price", "size", "source", name="uq_tas_prints"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime)  # when we pulled it (ET)
    ts: Mapped[datetime] = mapped_column(DateTime)  # vendor print time
    trade_date: Mapped[date] = mapped_column(Date)  # session day (grouping / prune)
    symbol: Mapped[str] = mapped_column(String(32))  # raw contract, e.g. .NVDA260619C230
    root: Mapped[str | None] = mapped_column(String(16))
    expiry: Mapped[date | None] = mapped_column(Date)
    strike: Mapped[float | None] = mapped_column(Float)
    cp: Mapped[str | None] = mapped_column(String(1))  # 'C' or 'P'
    side: Mapped[str | None] = mapped_column(String(8))  # buy / sell / mid / unknown
    price: Mapped[float | None] = mapped_column(Float)
    size: Mapped[int | None] = mapped_column(Integer)
    notional: Mapped[float | None] = mapped_column(Float)  # price * size * 100
    spot: Mapped[float | None] = mapped_column(Float)
    delta: Mapped[float | None] = mapped_column(Float)
    gamma: Mapped[float | None] = mapped_column(Float)
    vega: Mapped[float | None] = mapped_column(Float)
    theta: Mapped[float | None] = mapped_column(Float)
    iv: Mapped[float | None] = mapped_column(Float)
    condition: Mapped[str | None] = mapped_column(String(8))  # exchange_sale_conditions code
    is_sweep: Mapped[bool | None] = mapped_column(Boolean)  # ISO sweep (condition == 'I')
    is_block: Mapped[bool | None] = mapped_column(Boolean)  # negotiated / large block
    is_financing: Mapped[bool | None] = mapped_column(Boolean)  # deep-ITM call / LEAP (non-directional)
    leg_group: Mapped[str | None] = mapped_column(String(24))  # same-poll structure cluster id
    source: Mapped[str] = mapped_column(String(32), default="convex")


class TasDailyFlow(Base):
    """Per-(trade_date, root) daily roll-up of the option tape — the DURABLE layer.

    The EOD ``tas_daily_rollup`` job aggregates each session's ``tas_prints`` into
    one row per name and upserts it here. This survives the 30-day raw-print prune,
    so the accumulation/distribution scorecard can look back months. Buy vs sell
    premium + signed ``net_dollar_delta`` are the accumulation tell. Descriptive
    only — never a signal (FlashAlpha rule 4). Idempotent on (trade_date, root).
    """

    __tablename__ = "tas_daily_flow"
    __table_args__ = (
        UniqueConstraint("trade_date", "root", name="uq_tas_daily_flow"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date)
    root: Mapped[str] = mapped_column(String(16))
    prints: Mapped[int | None] = mapped_column(Integer)
    total_notional: Mapped[float | None] = mapped_column(Float)
    call_notional: Mapped[float | None] = mapped_column(Float)
    put_notional: Mapped[float | None] = mapped_column(Float)
    buy_notional: Mapped[float | None] = mapped_column(Float)
    sell_notional: Mapped[float | None] = mapped_column(Float)
    net_dollar_delta: Mapped[float | None] = mapped_column(Float)  # buy +, sell -
    gross_dollar_delta: Mapped[float | None] = mapped_column(Float)
    net_premium_call_put: Mapped[float | None] = mapped_column(Float)
    pct_buy: Mapped[float | None] = mapped_column(Float)
    dominant_side: Mapped[str | None] = mapped_column(String(8))  # buy / sell / mixed
    created_at: Mapped[datetime | None] = mapped_column(DateTime)


class TasDailyContract(Base):
    """Per-(trade_date, root, expiry, strike, cp) daily roll-up — repeat-contract grain.

    The contract-level companion to ``tas_daily_flow``: which exact strikes/expiries
    are being accumulated or distributed, kept long-term beyond the raw-print prune.
    ``buy_prints``/``sell_prints`` + ``dominant_side`` drive the repeat-contract view.
    Descriptive only (rule 4). Idempotent on (trade_date, root, expiry, strike, cp).
    """

    __tablename__ = "tas_daily_contract"
    __table_args__ = (
        UniqueConstraint(
            "trade_date", "root", "expiry", "strike", "cp",
            name="uq_tas_daily_contract",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date)
    root: Mapped[str] = mapped_column(String(16))
    expiry: Mapped[date | None] = mapped_column(Date)
    strike: Mapped[float | None] = mapped_column(Float)
    cp: Mapped[str | None] = mapped_column(String(1))
    n_prints: Mapped[int | None] = mapped_column(Integer)
    total_notional: Mapped[float | None] = mapped_column(Float)
    total_size: Mapped[int | None] = mapped_column(Integer)
    avg_price: Mapped[float | None] = mapped_column(Float)
    spot: Mapped[float | None] = mapped_column(Float)  # avg underlying at trade time
    avg_delta: Mapped[float | None] = mapped_column(Float)  # avg option delta
    buy_prints: Mapped[int | None] = mapped_column(Integer)
    sell_prints: Mapped[int | None] = mapped_column(Integer)
    buy_notional: Mapped[float | None] = mapped_column(Float)
    sell_notional: Mapped[float | None] = mapped_column(Float)
    net_dollar_delta: Mapped[float | None] = mapped_column(Float)
    dominant_side: Mapped[str | None] = mapped_column(String(8))
    created_at: Mapped[datetime | None] = mapped_column(DateTime)


# ── Dynamic (research-driven) watchlist ────────────────────────────────


class ResearchNote(Base):
    """Per-ticker narrative research note (PDF + 10-K + FMP + regime via LLM).

    Written nightly by the research-note job, stored on the NAS Postgres, one row
    per (symbol, as_of). Shown on the Research Watchlist page. Descriptive
    research read-through only (FlashAlpha rule 4).
    """

    __tablename__ = "research_notes"
    __table_args__ = (UniqueConstraint("symbol", "as_of", name="uq_research_notes"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16))
    as_of: Mapped[date] = mapped_column(Date)
    note_md: Mapped[str] = mapped_column(Text)
    sources: Mapped[str | None] = mapped_column(String(128))
    model: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime | None] = mapped_column(DateTime)

class SurfaceReport(Base):
    """Per-ticker interpretive surface + flow report (3-part narrative via LLM).

    Written nightly by the surface-report job from the latest oi_chain_eod
    snapshot (surface metrics), the option flow (stored flow_snapshots overnight),
    and KB grounding. One row per (symbol, as_of). Shown on the Vol Lab page so
    the slow CPU-Ollama generation happens overnight, not on page load.
    Descriptive regime read-through only (FlashAlpha rule 4).
    """

    __tablename__ = "surface_reports"
    __table_args__ = (UniqueConstraint("symbol", "as_of", name="uq_surface_reports"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16))
    as_of: Mapped[date] = mapped_column(Date)
    report_md: Mapped[str] = mapped_column(Text)
    flow_source: Mapped[str | None] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime | None] = mapped_column(DateTime)


class WatchlistEntry(Base):
    """A ticker surfaced from uploaded company research, with LLM rationale.

    Populated by the research-ingest pipeline: an uploaded report is run through
    the LLM, which extracts the tickers it discusses plus a one-line rationale,
    a sentiment (-1 bearish .. 1 bullish) and themes. One row per
    (symbol, source document). The Research Watchlist page lists these and
    cross-references whatever regime metrics exist for the symbol.

    ``symbol`` is intentionally NOT a FK (a researched name may not be in the
    collection watchlist / ``tickers`` yet). Descriptive context only — never a
    trade signal (FlashAlpha rule 4).
    """

    __tablename__ = "watchlist_entries"
    __table_args__ = (
        UniqueConstraint("symbol", "source_doc_id", name="uq_watchlist_entries"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16))
    source_doc_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"))
    rationale: Mapped[str | None] = mapped_column(Text)
    sentiment: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    themes: Mapped[list[str] | None] = mapped_column(JSON)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class FilingHolding(Base):
    """A 13F holding snapshot row (per fund CIK / report period / CUSIP).

    Banked by the ``filings_fetch`` job from SEC EDGAR 13F information tables so a
    fund's holdings can be diffed quarter-over-quarter (new / added / trimmed /
    exited) and surfaced to the research watchlist. A 13F carries CUSIP + issuer,
    not a ticker (``ticker`` is best-effort, resolved later). Descriptive research
    context only — never a trade signal (FlashAlpha rule 4).
    """

    __tablename__ = "filing_holdings"
    __table_args__ = (UniqueConstraint("cik", "period", "cusip", name="uq_filing_holdings"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cik: Mapped[str] = mapped_column(String(10))
    fund: Mapped[str | None] = mapped_column(String(64))
    period: Mapped[str] = mapped_column(String(10))  # report date, YYYY-MM-DD
    cusip: Mapped[str] = mapped_column(String(12))
    issuer: Mapped[str | None] = mapped_column(String(128))
    ticker: Mapped[str | None] = mapped_column(String(16))
    value_usd: Mapped[float | None] = mapped_column(Float)
    shares: Mapped[float | None] = mapped_column(Float)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SwingFeature(Base):
    """Daily per-name swing feature snapshot (one row per symbol per trading day).

    Banks the Stage-1 feature vector so its percentile features (IV-rank, IV/RV,
    skew, GEX/DEX positioning) mature into a real trailing distribution for the
    Stage-2 fitted model (see the swing-trade-system build). Written on demand /
    daily by ``scripts/swing_features.py`` from CVForge (ADR-004) — convexlib is
    untouched. Percentiles are standardized against the name's own trailing 252d
    from this table (today excluded), matching the ``skew_snapshots`` contract.

    Descriptive features only — not signals (FlashAlpha rule 4). The validated
    ``SignalGenerator`` (P4) under ``strategies/`` is the only writer to ``signals``.
    """

    __tablename__ = "swing_features"
    __table_args__ = (UniqueConstraint("symbol", "ts", name="uq_swing_features"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), ForeignKey("tickers.symbol"))
    ts: Mapped[date] = mapped_column(Date)  # trading day

    # Raw daily features (decimals unless noted).
    spot: Mapped[float | None] = mapped_column(Float)
    atm_iv: Mapped[float | None] = mapped_column(Float)
    rv20: Mapped[float | None] = mapped_column(Float)
    iv_rv: Mapped[float | None] = mapped_column(Float)  # atm_iv / rv20
    rsi14: Mapped[float | None] = mapped_column(Float)
    sma50: Mapped[float | None] = mapped_column(Float)
    px_vs_sma50: Mapped[float | None] = mapped_column(Float)  # spot / sma50 - 1
    skew_25d: Mapped[float | None] = mapped_column(Float)  # 25d put_iv - call_iv
    gex: Mapped[float | None] = mapped_column(Float)
    dex: Mapped[float | None] = mapped_column(Float)

    # Trailing-252d percentiles (0..1, min_history 20; NULL until history banks).
    atm_iv_rank_252d: Mapped[float | None] = mapped_column(Float)
    iv_rv_pctile_252d: Mapped[float | None] = mapped_column(Float)
    skew_pctile_252d: Mapped[float | None] = mapped_column(Float)
    gex_pctile_252d: Mapped[float | None] = mapped_column(Float)
    dex_pctile_252d: Mapped[float | None] = mapped_column(Float)


class FundamentalsSnapshot(Base):
    """Weekly per-name fundamental inputs + cross-sectional factor scores.

    Banked by ``scheduler/jobs/factor_scores.py`` from CVForge FMP fundamentals
    (ADR-005 — no new vendor). Raw ratio/growth/momentum inputs plus the
    universe-relative factor z-scores (Value/Quality/Growth/Momentum/Risk) and the
    weighted composite. Unique on (symbol, ts) for idempotent weekly upserts
    (rule 5). Descriptive research scores only (FlashAlpha rule 4).
    """

    __tablename__ = "fundamentals_snapshots"
    __table_args__ = (UniqueConstraint("symbol", "ts", name="uq_fundamentals_snapshots"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), ForeignKey("tickers.symbol"))
    ts: Mapped[date] = mapped_column(Date)
    # Raw inputs (ratios/margins as decimals; returns as fractions).
    pe: Mapped[float | None] = mapped_column(Float)
    pb: Mapped[float | None] = mapped_column(Float)
    ps: Mapped[float | None] = mapped_column(Float)
    ev_ebitda: Mapped[float | None] = mapped_column(Float)
    roe: Mapped[float | None] = mapped_column(Float)
    roic: Mapped[float | None] = mapped_column(Float)
    gross_margin: Mapped[float | None] = mapped_column(Float)
    net_margin: Mapped[float | None] = mapped_column(Float)
    fcf_margin: Mapped[float | None] = mapped_column(Float)
    debt_to_equity: Mapped[float | None] = mapped_column(Float)
    current_ratio: Mapped[float | None] = mapped_column(Float)
    revenue_growth: Mapped[float | None] = mapped_column(Float)
    eps_growth: Mapped[float | None] = mapped_column(Float)
    beta: Mapped[float | None] = mapped_column(Float)
    ret_3m: Mapped[float | None] = mapped_column(Float)
    ret_12m: Mapped[float | None] = mapped_column(Float)
    # Cross-sectional factor z-scores + composite (universe-relative).
    value_score: Mapped[float | None] = mapped_column(Float)
    quality_score: Mapped[float | None] = mapped_column(Float)
    growth_score: Mapped[float | None] = mapped_column(Float)
    momentum_score: Mapped[float | None] = mapped_column(Float)
    risk_score: Mapped[float | None] = mapped_column(Float)
    composite_score: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str | None] = mapped_column(String(32))


class SentimentSnapshot(Base):
    """Weekly per-name sentiment: institutional 13F + analyst ratings/targets.

    Banked by ``scheduler/jobs/sentiment.py`` from CVForge FMP (ADR-005 — no new
    vendor): institutional-ownership (latest 13F quarter), price-target-consensus,
    grades-consensus, quote. Raw fields + two pure derivations (implied upside to the
    average target, Buy-share of the panel). Unique on (symbol, ts) for the idempotent
    weekly upsert (rule 5). Descriptive descriptors only (FlashAlpha rule 4) — the
    *trend* (target-cut rate, institutional accumulation) is the signal, not one row.
    """

    __tablename__ = "sentiment_snapshots"
    __table_args__ = (UniqueConstraint("symbol", "ts", name="uq_sentiment_snapshots"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), ForeignKey("tickers.symbol"))
    ts: Mapped[date] = mapped_column(Date)
    # Institutional (latest 13F quarter; counts/shares stored as Float per convention).
    inst_pct: Mapped[float | None] = mapped_column(Float)
    inst_holders: Mapped[float | None] = mapped_column(Float)
    inst_shares: Mapped[float | None] = mapped_column(Float)
    inst_net_share_change: Mapped[float | None] = mapped_column(Float)
    inst_new_positions: Mapped[float | None] = mapped_column(Float)
    inst_closed_positions: Mapped[float | None] = mapped_column(Float)
    inst_put_call: Mapped[float | None] = mapped_column(Float)
    # Analyst price targets + rating panel.
    pt_avg: Mapped[float | None] = mapped_column(Float)
    pt_high: Mapped[float | None] = mapped_column(Float)
    pt_low: Mapped[float | None] = mapped_column(Float)
    rating_buy: Mapped[float | None] = mapped_column(Float)
    rating_hold: Mapped[float | None] = mapped_column(Float)
    rating_sell: Mapped[float | None] = mapped_column(Float)
    num_analysts: Mapped[float | None] = mapped_column(Float)
    rating_consensus: Mapped[str | None] = mapped_column(String(16))
    price: Mapped[float | None] = mapped_column(Float)
    # Pure derivations.
    pt_upside_pct: Mapped[float | None] = mapped_column(Float)
    buy_share: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str | None] = mapped_column(String(32))


class SurfaceSnapshot(Base):
    """Near-money per-STRIKE IV surface for index ETFs, banked daily (fixed-strike).

    One row per (symbol, ts, expiry_date, strike): the OTM-wing IV at each near-money
    listed strike (|delta| ~0.05..0.95), plus the stored ``delta`` (a delta view is
    derivable) and ``spot``. Keyed by STRIKE so day-over-day changes and the vol
    footprint track the SAME contract — fixed strike is the receipt; fixed delta gets
    smeared as spot slides along the skew. Written by
    ``scheduler/jobs/surface_snapshots.py`` from the live chain (SPX/QQQ/SPY). Unique on
    (symbol, ts, expiry_date, strike) for the idempotent upsert (rule 5). Descriptor only
    (FlashAlpha rule 4).
    """

    __tablename__ = "surface_snapshots"
    __table_args__ = (
        UniqueConstraint("symbol", "ts", "expiry_date", "strike", name="uq_surface_snapshots"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), ForeignKey("tickers.symbol"))
    ts: Mapped[date] = mapped_column(Date)
    expiry_date: Mapped[date] = mapped_column(Date)
    dte: Mapped[int] = mapped_column(Integer)
    strike: Mapped[float] = mapped_column(Float)
    iv: Mapped[float | None] = mapped_column(Float)
    delta: Mapped[float | None] = mapped_column(Float)
    spot: Mapped[float | None] = mapped_column(Float)


class EstimateSnapshot(Base):
    """Weekly analyst EPS/revenue estimate snapshot (CVForge FMP).

    One row per (symbol, ts) for the nearest upcoming fiscal period, banked
    forward so the *revision* (this week's estimate vs a prior week's) can be
    read as the trend — the highest-quality of the alignment signals. Written by
    ``scheduler/jobs/estimate_snapshots.py``. Descriptive descriptor only
    (FlashAlpha rule 4).
    """

    __tablename__ = "estimate_snapshots"
    __table_args__ = (UniqueConstraint("symbol", "ts", name="uq_estimate_snapshots"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), ForeignKey("tickers.symbol"))
    ts: Mapped[date] = mapped_column(Date)
    period_date: Mapped[date | None] = mapped_column(Date)  # fiscal period the estimate is for
    eps_avg: Mapped[float | None] = mapped_column(Float)
    eps_high: Mapped[float | None] = mapped_column(Float)
    eps_low: Mapped[float | None] = mapped_column(Float)
    eps_num: Mapped[float | None] = mapped_column(Float)  # number of analysts
    revenue_avg: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str | None] = mapped_column(String(32))


class SectorCorrSnapshot(Base):
    """Daily realized sector-ETF correlation regime (11 SPDRs) — descriptor only.

    Realized complement to the option-implied COR1M/COR3M in the VIX complex:
    21d/63d average PAIRWISE correlation (off-diagonal only) + cross-sectional
    dispersion, plus the latest correlation matrix. One row per (as_of, source).
    """

    __tablename__ = "sector_corr_snapshots"
    __table_args__ = (UniqueConstraint("as_of", "source", name="uq_sector_corr_snap"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    as_of: Mapped[date] = mapped_column(Date)
    avg_corr_21: Mapped[float | None] = mapped_column(Float)
    avg_corr_63: Mapped[float | None] = mapped_column(Float)
    dispersion: Mapped[float | None] = mapped_column(Float)
    n_etfs: Mapped[int | None] = mapped_column(Integer)
    matrix: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    computed_at: Mapped[datetime | None] = mapped_column(DateTime)
    source: Mapped[str] = mapped_column(String(32), default="yfinance")


class SectorSnapshot(Base):
    """Per-SPDR sector-layer snapshot (CVForge) — skew + walls + fixed-strike IV grid.

    Complements the aggregate ``greeks_snapshots`` row (built from the SAME
    CVForge chain): the 25Δ risk-reversal, the call/put gamma walls, and the
    near-money per-strike IV grid whose day-over-day diff is the fixed-strike
    "offered vs bid" footprint (wall holds vs breaks — "a wall is not a wall").
    One row per (as_of, symbol, source). Descriptor only (FlashAlpha rule 4).
    """

    __tablename__ = "sector_snapshots"
    __table_args__ = (UniqueConstraint("as_of", "symbol", "source", name="uq_sector_snap"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    as_of: Mapped[date] = mapped_column(Date)
    symbol: Mapped[str] = mapped_column(String(16))
    spot: Mapped[float | None] = mapped_column(Float)
    net_gex: Mapped[float | None] = mapped_column(Float)
    net_dex: Mapped[float | None] = mapped_column(Float)
    gex_flip: Mapped[float | None] = mapped_column(Float)
    atm_iv: Mapped[float | None] = mapped_column(Float)
    rr25: Mapped[float | None] = mapped_column(Float)
    rr25_dte: Mapped[int | None] = mapped_column(Integer)
    call_wall: Mapped[float | None] = mapped_column(Float)
    put_wall: Mapped[float | None] = mapped_column(Float)
    strike_iv: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    computed_at: Mapped[datetime | None] = mapped_column(DateTime)
    source: Mapped[str] = mapped_column(String(32), default="cvforge")
