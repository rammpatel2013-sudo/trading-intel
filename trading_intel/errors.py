"""Domain error hierarchy for trading-intel.

Per CLAUDE.md (Errors section):
- Domain errors subclass ``TradingIntelError``.
- External-service errors are caught at the ``clients/`` boundary and
  re-raised as ``TradingIntelError`` subclasses with the original as ``__cause__``.
"""
from __future__ import annotations


class TradingIntelError(Exception):
    """Root of the trading-intel domain error hierarchy."""


class DataSourceError(TradingIntelError):
    """An external options-data vendor failed or returned unusable data."""


class ComputationError(TradingIntelError):
    """A Greek/exposure computation could not be completed."""
