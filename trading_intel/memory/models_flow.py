"""ORM models for the flow bank (2026-11-19, flow catalog items #1-#4).

Same ``Base`` as ``models.py``; created by the hand-written migration ``0060_flow_bank`` (never
autogenerate). Descriptor tables only (rule 4): nothing here writes to ``signals``.
Source map: project doc ``claude/flow-params-catalog-20261119.md``.

Tables
------
flow_und_daily   Convex ``und`` screener after the close: every optionable name's option flow
                 (buy/sell by $, contracts, delta, gamma, vega; call/put; ratios; no-side volume).
                 The buy/sell counters reset ~18:00 ET, so this is the only capture.
flowchart_15s    Convex ``flowchart`` 15-second series (flownet, vflownet, call/put bought minus
                 sold, call/put gxoi/dxoi/vxoi/txoi, prop1-4). Convex serves TODAY only.
od_flow_daily    optionsdata.ai flow per underlying / expiration x right x category
                 (regular | spread | eth | eth_spread) x side (buy | sell | undecided).
bars_1m          1-minute OHLCV bars (ETFs) from FMP via CVForge, naive Eastern ``ts``.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from trading_intel.memory.models import Base

# und fields kept (order = request order after "symbol"). Units: value_* = option premium $,
# volm_* = contracts, deltas_/gammas_/vegas_* = greek x contracts as Convex sums them,
# day_volume = SHARES of the underlying. "und" = no side ("undefined"), not "underlying".
UND_FIELDS: tuple[str, ...] = tuple(
    "price change day_volume option_volume call_volume put_volume put_call_ratio volatility "
    "front_volatility back_volatility value volm value_buy value_sell value_call_buy "
    "value_call_sell value_put_buy value_put_sell volm_buy volm_sell volm_call_buy volm_call_sell "
    "volm_put_buy volm_put_sell deltas_buy deltas_sell deltas_call_buy deltas_call_sell "
    "deltas_put_buy deltas_put_sell gammas_buy gammas_sell vegas_buy vegas_sell value_und volm_und "
    "value_bs volm_bs flowratio vflowratio value_call_ratio value_put_ratio volm_call_ratio "
    "volm_put_ratio gxoi dxoi vxoi vannaxoi charmxoi gxvolm dxvolm vannaxvolm charmxvolm".split()
)

FLOWCHART_COLS: tuple[str, ...] = tuple(
    "flownet vflownet prop1 prop2 prop3 prop4 value_call_bs value_put_bs volm_call_bs "
    "volm_put_bs call_gxoi put_gxoi call_dxoi put_dxoi call_vxoi put_vxoi call_txoi "
    "put_txoi".split()
)


def _float_cols(names: tuple[str, ...]) -> dict:
    return {n: mapped_column(n, Float) for n in names}


# Built with type() so the long field lists stay in one place (UND_FIELDS / FLOWCHART_COLS).
FlowUndDaily = type(
    "FlowUndDaily",
    (Base,),
    {
        "__tablename__": "flow_und_daily",
        "date": mapped_column(Date, primary_key=True),
        "symbol": mapped_column(String(16), primary_key=True, index=True),
        "captured_at": mapped_column(DateTime),  # naive Eastern
        **_float_cols(UND_FIELDS),
    },
)

FlowChart15s = type(
    "FlowChart15s",
    (Base,),
    {
        "__tablename__": "flowchart_15s",
        "symbol": mapped_column(String(16), primary_key=True),
        "ts": mapped_column(DateTime, primary_key=True),  # naive Eastern
        "trade_date": mapped_column(Date, index=True),
        **_float_cols(FLOWCHART_COLS),
    },
)


class OdFlowDaily(Base):
    __tablename__ = "od_flow_daily"
    __table_args__ = (
        UniqueConstraint(
            "date",
            "symbol",
            "expiration",
            "right",
            "category",
            "side",
            name="uq_od_flow_daily",
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    symbol: Mapped[str] = mapped_column(String(16))
    expiration: Mapped[date | None] = mapped_column(Date)  # NULL = whole underlying
    right: Mapped[str] = mapped_column(String(4))  # call | put
    category: Mapped[str] = mapped_column(String(12))  # regular | spread | eth | eth_spread
    side: Mapped[str] = mapped_column(String(10))  # buy | sell | undecided
    premium_usd: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)  # contracts
    count: Mapped[float | None] = mapped_column(Float)  # prints


class Bars1m(Base):
    __tablename__ = "bars_1m"

    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, primary_key=True)  # naive Eastern, bar start
    source: Mapped[str] = mapped_column(String(16), primary_key=True)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)  # shares


FLOW_TABLES = (FlowUndDaily, FlowChart15s, OdFlowDaily, Bars1m)
