"""LETF net creation/redemption (issuance) flow descriptors.

The descriptor layer over ``letf_shares_snapshots`` (migration 0032, ingested by
``scheduler/jobs/letf_flows.py``). ``registry`` carries the per-symbol leverage /
issuer / underlying reference data; ``descriptors`` turns the banked
(shares_outstanding, NAV) series into dshares, net issuance $, AUM, and the
k*(k-1)*AUM*return forced-rebalance estimate.

Regime descriptor only (FlashAlpha rule 4) — never a signal source on its own.
"""

from __future__ import annotations

from trading_intel.etf_flows.descriptors import (
    FlowPoint,
    SharesRow,
    bucket_totals,
    compute_symbol_flows,
    latest_point,
)
from trading_intel.etf_flows.registry import REGISTRY, LetfMeta, meta_for

__all__ = [
    "REGISTRY",
    "FlowPoint",
    "LetfMeta",
    "SharesRow",
    "bucket_totals",
    "compute_symbol_flows",
    "latest_point",
    "meta_for",
]
