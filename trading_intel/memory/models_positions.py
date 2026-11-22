"""Position tables (2026-11-21). DERIVED and rebuildable from tas_prints + the nightly optionsdata.ai OI files.

pos_packages   one row per SPX spread package EVENT per session (vertical / calendar; a roll = two linked rows):
               legs (dear / cheap), lots, net price, side decided once per package (+1 bought = long the dear leg,
               -1 sold), action from the next-session OI change (open / close / roll_close / roll_open / unknown)
               and ``roll_key`` linking the two halves of a roll (the holder carried to the new expiry).
               See trading_intel/positions/packages.py for the method. Created by migration 0069_pos_packages.
decay_line_marks  one row per session x mark (10:00 / 13:00 / 15:50) x variant (N / W / L): where decay of the
               week's expiring SPX options flips dealers buy <-> sell, the hedge at spot, and the score (did SPX
               move the hedge's way into the close). Background test before any MAP change (2026-11-21,
               trading_intel/market/decay_line.py). Created by migration 0071_decay_line_marks.
pos_book_daily  (2026-11-22) contract per session: OI-anchored customer position estimate. Size from the OI change
               (exact), side from the tape (+ package legs, + strike-IV vs ATM-IV fallback). Re-anchored to OI every
               day so it never drifts; ``confidence`` = share of OI whose side we measured. trading_intel/positions/book.py.
tape_coverage_daily  (2026-11-22) root per session: how much of the SPX tape we captured (vs chain volume and vs
               the 20-session median) and empty RTH half-hours -> alert on silent holes.
flow_players / flow_player_sightings  (2026-11-22) named repeat traders (JPM Collar, Year-End Put Whale, 0DTE Whale,
               ...) and every time one is seen; unknown repeaters get an ``auto-*`` slug for Mithil to name.
               trading_intel/positions/players.py. All four created by migration 0072_pos_book.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from trading_intel.memory.models import Base


class PosPackage(Base):
    __tablename__ = "pos_packages"
    __table_args__ = (UniqueConstraint("symbol", "trade_date", "pkg_key", name="uq_pos_packages"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(8), default="SPX")
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    pkg_key: Mapped[str] = mapped_column(String(96))              # structure|dear leg|cheap leg|first fill HHMMSS
    structure: Mapped[str] = mapped_column(String(12))            # vertical / calendar
    cp: Mapped[str] = mapped_column(String(1))
    dear_root: Mapped[str] = mapped_column(String(8))
    dear_expiry: Mapped[date] = mapped_column(Date, index=True)
    dear_strike: Mapped[float] = mapped_column(Float)
    cheap_root: Mapped[str] = mapped_column(String(8))
    cheap_expiry: Mapped[date] = mapped_column(Date)
    cheap_strike: Mapped[float] = mapped_column(Float)
    first_ts: Mapped[datetime] = mapped_column(DateTime)          # naive Eastern
    last_ts: Mapped[datetime] = mapped_column(DateTime)
    lots: Mapped[int] = mapped_column(Integer)
    fills: Mapped[int] = mapped_column(Integer)
    net: Mapped[float | None] = mapped_column(Float)              # dear minus cheap, per package, lots-weighted
    side: Mapped[int] = mapped_column(Integer)                    # +1 bought, -1 sold, 0 undecided (customer)
    vote: Mapped[float] = mapped_column(Float)
    action: Mapped[str] = mapped_column(String(12))               # open / close / roll_close / roll_open / unknown
    oi_score: Mapped[float | None] = mapped_column(Float)         # mean clip(dOI / lots) over the legs
    roll_key: Mapped[str | None] = mapped_column(String(96))      # the other half of a roll
    source: Mapped[str] = mapped_column(String(16), default="convex_tape")
    ingested_at: Mapped[datetime | None] = mapped_column(DateTime)  # naive Eastern


class DecayLineMark(Base):
    __tablename__ = "decay_line_marks"
    __table_args__ = (UniqueConstraint("trade_date", "mark", "variant", name="uq_decay_line_marks"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    mark: Mapped[str] = mapped_column(String(5))           # "10:00" / "13:00" / "15:50" Eastern
    variant: Mapped[str] = mapped_column(String(1))        # N / W / L
    spot: Mapped[float | None] = mapped_column(Float)      # SPX at the mark (tape)
    close: Mapped[float | None] = mapped_column(Float)     # official close (equity_bars)
    es_at_spot: Mapped[float | None] = mapped_column(Float)  # dealer decay hedge at spot, ES (+ buy)
    flip_below: Mapped[float | None] = mapped_column(Float)  # nearest sign change below spot
    flip_above: Mapped[float | None] = mapped_column(Float)  # nearest sign change above spot
    flips: Mapped[str | None] = mapped_column(String(200))   # up to 6 nearest sign changes, comma list
    move: Mapped[float | None] = mapped_column(Float)      # close - spot
    hit: Mapped[bool | None] = mapped_column(Boolean)      # sign(es_at_spot) == sign(move)
    ingested_at: Mapped[datetime | None] = mapped_column(DateTime)


class PosBookDaily(Base):
    __tablename__ = "pos_book_daily"
    __table_args__ = (UniqueConstraint("date", "symbol", "root", "expiry", "strike", "cp", name="uq_pos_book_daily"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, index=True)         # the session whose trades this row includes
    symbol: Mapped[str] = mapped_column(String(8), default="SPX")
    root: Mapped[str] = mapped_column(String(8))                 # SPXW (PM) / SPX (AM monthly)
    expiry: Mapped[date] = mapped_column(Date)
    strike: Mapped[float] = mapped_column(Float)
    cp: Mapped[str] = mapped_column(String(1))
    oi: Mapped[float | None] = mapped_column(Float)              # OI after the session (contracts)
    d_oi: Mapped[float | None] = mapped_column(Float)            # OI change made by the session
    tape_buy: Mapped[float | None] = mapped_column(Float)        # customer-bought single-leg contracts (quote-weighted)
    tape_sell: Mapped[float | None] = mapped_column(Float)
    pkg_buy: Mapped[float | None] = mapped_column(Float)         # package legs (pos_packages), customer side
    pkg_sell: Mapped[float | None] = mapped_column(Float)
    iv_lean: Mapped[float | None] = mapped_column(Float)         # -0.5 / 0 / +0.5 from strike IV vs ATM IV change
    lean: Mapped[float | None] = mapped_column(Float)            # -1..+1 side used for the session (+ = customers bought)
    cust_pos: Mapped[float | None] = mapped_column(Float)        # customers net long (+) / short (-), contracts
    known: Mapped[float | None] = mapped_column(Float)           # contracts of OI whose side was measured
    seed_oi: Mapped[float | None] = mapped_column(Float)         # OI already open when we first saw it (never signed)
    confidence: Mapped[float | None] = mapped_column(Float)      # known / oi, 0..1
    gamma: Mapped[float | None] = mapped_column(Float)           # per contract, from the next 08:35 book
    spot: Mapped[float | None] = mapped_column(Float)
    mm_gamma_usd_1pct: Mapped[float | None] = mapped_column(Float)        # dealer gamma $ per 1% (dealers = -customers)
    naive_mm_gamma_usd_1pct: Mapped[float | None] = mapped_column(Float)  # naive book (calls +, puts -) for compare
    player_slug: Mapped[str | None] = mapped_column(String(64))
    ingested_at: Mapped[datetime | None] = mapped_column(DateTime)


class TapeCoverageDaily(Base):
    __tablename__ = "tape_coverage_daily"
    __table_args__ = (UniqueConstraint("date", "root", name="uq_tape_coverage_daily"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date)
    root: Mapped[str] = mapped_column(String(8))                 # SPX+SPXW summed as "SPX*"
    prints: Mapped[int | None] = mapped_column(Integer)
    contracts: Mapped[float | None] = mapped_column(Float)
    chain_volume: Mapped[float | None] = mapped_column(Float)    # Convex closing-book day volume, when stored
    coverage_pct: Mapped[float | None] = mapped_column(Float)    # contracts / chain_volume * 100
    vs_median_pct: Mapped[float | None] = mapped_column(Float)   # contracts / 20-session median * 100
    first_et: Mapped[str | None] = mapped_column(String(5))
    last_et: Mapped[str | None] = mapped_column(String(5))
    empty_rth_slots: Mapped[str | None] = mapped_column(String(200))  # RTH half-hours with no prints, "10:30,11:00"
    status: Mapped[str | None] = mapped_column(String(8))        # ok / thin / hole
    ingested_at: Mapped[datetime | None] = mapped_column(DateTime)


class FlowPlayer(Base):
    __tablename__ = "flow_players"
    __table_args__ = (UniqueConstraint("slug", name="uq_flow_players"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(64))
    name: Mapped[str | None] = mapped_column(String(80))         # Mithil's name for it (seeded ones pre-named)
    description: Mapped[str | None] = mapped_column(String(400))
    status: Mapped[str | None] = mapped_column(String(12))       # named / unnamed / single
    first_seen: Mapped[date | None] = mapped_column(Date)
    last_seen: Mapped[date | None] = mapped_column(Date)
    n_sightings: Mapped[int | None] = mapped_column(Integer)
    ingested_at: Mapped[datetime | None] = mapped_column(DateTime)


class FlowPlayerSighting(Base):
    __tablename__ = "flow_player_sightings"
    __table_args__ = (UniqueConstraint("date", "player_slug", "ref", name="uq_flow_player_sightings"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    player_slug: Mapped[str] = mapped_column(String(64))
    ref: Mapped[str] = mapped_column(String(120))                # pkg_key, or root|expiry|strike|cp for a line
    structure: Mapped[str | None] = mapped_column(String(12))    # single / vertical / calendar
    cp: Mapped[str | None] = mapped_column(String(1))
    expiry: Mapped[date | None] = mapped_column(Date)
    strike: Mapped[float | None] = mapped_column(Float)
    strike2: Mapped[float | None] = mapped_column(Float)
    dte: Mapped[int | None] = mapped_column(Integer)
    otm_pct: Mapped[float | None] = mapped_column(Float)         # strike / spot - 1, percent
    lots: Mapped[float | None] = mapped_column(Float)
    side: Mapped[int | None] = mapped_column(Integer)            # +1 customer bought, -1 sold
    action: Mapped[str | None] = mapped_column(String(12))       # open / close / roll_open / ... / build
    premium_usd: Mapped[float | None] = mapped_column(Float)
    clips: Mapped[int | None] = mapped_column(Integer)           # prints in the day (accumulation pattern)
    fingerprint: Mapped[str | None] = mapped_column(String(80))
    ingested_at: Mapped[datetime | None] = mapped_column(DateTime)
