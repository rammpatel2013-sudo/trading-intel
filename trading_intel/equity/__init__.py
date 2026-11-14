"""Equity / swing side (build-queue E1-E6, started 2026-11-14).

Layers, bottom up:

* ``universe`` / ``parse`` / ``store``  - E1 foundation: S&P 1500 members (S&P 500 point-in-time),
  daily bars + market cap, quarterly fundamentals with the day they became public, profiles.
* ``panel``                             - dense date x symbol arrays built from the tables.
* ``nhnl`` / ``damage`` / ``gate``      - E2 "is it safe to buy stocks?" (green / amber / red).
* ``styles`` / ``factor_model``         - E3 factor risk model (toraniko method, numpy only):
  daily market / sector / style returns and each stock's own move.
* ``candidates``                        - E4 leaders by own move, trend filter, coiled springs.
* ``trade_card`` / ``risk``             - E5 debit-spread card + hedge menu, E6 sizing / stops /
  book factor check.
* ``report``                            - plain-words HTML + Telegram summary.

Descriptors only (rule 4): nothing here writes to ``signals``.
"""
