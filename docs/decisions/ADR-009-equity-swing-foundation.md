# ADR-009: Equity / swing foundation - S&P 1500 data on FMP, thetrading.tools new highs/lows, own factor model

- Date: 2026-11-14 (Mithil: "start building equity/swing side whenever we can" - "build entire thing, no stopping")
- Status: accepted
- Context: the swing / long-term side needs a stock universe with history (prices, market caps, fundamentals that
  were public on each date), a "safe to buy stocks?" read, a factor model that separates a stock's own strength
  from market / sector / style tides, and candidate lists. The 12-name factor scoring (ADR-005) cannot do this.
- Decision:
  1. **FMP via the CVForge key (no new vendor, ADR-004/005).** New client `clients/equity_data.py` uses only
     endpoints probed live through the passthrough on 2026-11-14: `sp500-constituent`,
     `historical-sp500-constituent` (S&P 500 point in time), `etf/holdings` (IJH / IJR = S&P 400 / 600 today),
     `historical-price-eod/full`, quarterly `income-statement` / `balance-sheet-statement` /
     `cash-flow-statement`, `profile`. Not served by the passthrough: `batch-quote`, `eod-bulk`, `*-bulk`,
     `sp400-constituent`. Calls capped at 8/s on a 6-thread pool.
  2. **thetrading.tools `nh_nl.json` (new free source, no key)** for NYSE / Nasdaq new highs - new lows
     (`clients/thetradingtools.py`, one GET a day). Licence: personal / research use with attribution, no
     commercial redistribution -> rows live only in the NAS DB, never in this public repo. Their universe is
     today's listings (survivorship): read today, never backtest on it. Partial vendor days are flagged
     `suspect` and cast no vote.
  3. **Factor risk model = the toraniko method, re-implemented in numpy** (polars was dropped in O1; no new
     dependency): daily cross-sectional WLS on sqrt(market cap), sector dummies constrained to sum to zero
     cap-weighted, seven styles (momentum, size, value, beta, volatility, quality, growth) as of the prior close.
     The residual is each stock's own move.
- Consequences: 10 new tables (migration 0057, additive). One-off backfill ~10k FMP calls; daily job ~1.5k
  calls + one GET. Gate thresholds (rule v1) are untested starting points. Descriptors only (rule 4): nothing
  writes to `signals`. Drop thetrading.tools if its file goes stale or keeps failing the suspect check.
