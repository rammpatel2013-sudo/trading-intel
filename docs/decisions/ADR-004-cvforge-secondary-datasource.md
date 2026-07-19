# ADR-004: Add CVForge as a secondary OptionsDataSource (breadth, history, FMP)

**Date:** 2026-07-12
**Status:** Accepted
**Deciders:** Mithil

## Context

ConvexValue (`convexlib`, email+password) is the primary options data source, but the vendor caps us at ~7 requests/min (`CONVEX_MAX_PER_MIN`), which limits collection to the ~12-name watchlist + indices. It has no per-contract historical option price series, and our FMP client (`clients/fmp.py`) is dormant (malformed `.env` key).

CVForge (cvforge.convexvalue.com) is ConvexValue's AI-tooling product exposing the **same backend** (`tap.convexvalue.com/api/data`) as a REST + MCP API keyed by a `cv_live_…` token. Mithil holds an annual **Research** subscription:

- 50,000 req/hr (~830/min) vs convexlib's 7/min
- historical option OHLC — daily + intraday bars (`/mas`)
- market-wide screener (`/screen`) + read-only DuckDB SQL (`/query`) over a ~1.96M-contract snapshot (`options_snapshots`, ~5,900 underlyings)
- 157 FMP endpoints — fundamentals, statements, estimates, earnings transcripts, technical indicators, macro

Verified live 2026-07-12. The one gap: CVForge's per-contract snapshot exposes **delta/gamma/theta/vega + IV only** — no vanna, no charm, no vxoi — whereas convexlib provides those natively and `greeks/exposures.py` needs `vanna`/`charm` columns to compute VEX/CHEX.

## Decision

Add `clients/cvforge.py` (`CVForgeClient`) as a **secondary** `OptionsDataSource`. convexlib remains **primary** for the live regime engine.

1. **Synthesize the missing greeks.** `CVForgeClient.chain()` computes `vanna` and `charm` per contract via `greeks/black_scholes.bs_vanna` / `bs_charm`, and derives `gxoi = gamma·oi`, `dxoi = delta·oi`, `vxoi = vega·oi`, so the normalized chain satisfies the Protocol and feeds `compute_exposures` unchanged. This extends the ADR-002 precedent (recomputing BS greeks for views convexlib doesn't serve) from simulation to a second vendor.
2. **Roles.** CVForge is used for: (a) market-wide breadth (`/screen`, `/query`) the 7/min convex cap can't reach — the watchlist stays deep, market-wide is a lighter opportunistic scan; (b) historical option OHLC for backtesting; (c) FMP (fundamentals, transcripts, technicals, macro), replacing the dormant `fmp.py` path — **no** new financialdatasets.ai vendor.
3. **Secrets.** Key in `.env` as `CVFORGE_API_KEY` (`SecretStr` on `Settings`); base URL `https://tap.convexvalue.com/api/data`. Never logged (rule 2).
4. **No cloud LLM.** CVForge's `/ai` gateway is out of scope — rule 7 keeps scheduled LLM on local Ollama.

## Consequences

- **Unit calibration required before the CVForge exposure path goes live.** CVForge greeks are standard BS units; Convex's raw `vanna`/`charm` scale is empirical. VEX/CHEX from CVForge must be calibrated against Convex on a shared name (e.g. AAPL) so dashboard figures are comparable. Tracked as a P2 task. (`charm` unit note: `exposures.CHEX` multiplies by 365, so the synthesized `charm` column must be **per-day** to match Convex; `bs_charm` here returns per-year → divide by 365 when building the column.)
- **Historical is price-only.** `/mas` returns option *price* OHLC, not historical chains/IV, so it cannot rebuild the June-2026 lost regime history and can only partially backtest signals (structure P&L yes; full signal replay no — percentile features must accumulate forward).
- Same vendor (ConvexValue backend), so no new-vendor tripwire; documented here for the record. Annual Research sub already owned — zero marginal cost within rate limits.
- OI still updates once daily post-close; snapshot refreshes ~1/min. Fine for EOD/regime, not tick-level.
