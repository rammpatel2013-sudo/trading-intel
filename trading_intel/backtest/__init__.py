"""Backtesting harness for trading-intel signals.

Validates strategy + regime outputs against historical forward returns. The
backtest layer is read-only — it never writes to ``signals`` or any other
analytics table. Outputs are dataclass-shaped results meant for offline
analysis and ADR-grade decision support (``docs/decisions/``).
"""
