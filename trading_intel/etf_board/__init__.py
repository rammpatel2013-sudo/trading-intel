"""ETF board (2026-11-19) - Mithil's ToS "Alt ETF Selects" watchlist rebuilt on our data.

Spec: project doc ``claude/etf-board-spec-20261119.md``. Every number comes from NAS Postgres tables
(equity_bars, bars_1m, flow_und_daily); the collect step writes those, the build step reads only the DB.
Descriptors only (rule 4): nothing here writes to ``signals``.
"""

ETF_LIST: tuple[str, ...] = tuple(
    "UNG PSQ IVOL BUG SH PHO BTAL BWET TAIL LTPZ VXX FXA PKW ROKT CGW OIH BOAT RSP MLPX USMV ARKG VFMF "
    "BDRY KMLM FCG IBB TBF AMLP QQQ SPY IHE XPH BRK-B AOR DBA MOO BNO CTA PDBC CRAK UGA XOP XLE XAR HYG "
    "JEDI IEO SHLD".split()
)
TRADES_24H: frozenset[str] = frozenset(
    "UNG PSQ SH LTPZ VXX RSP USMV ARKG TBF QQQ SPY AOR DBA MOO PDBC XOP XLE IEO".split()
)
TAG = "alt_etf_selects"
CORR_REF = "QQQ"
