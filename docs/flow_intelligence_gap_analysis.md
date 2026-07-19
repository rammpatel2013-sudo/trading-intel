# Flow-intelligence view — build from what we have + data gaps

*2026-07-16. Prompted by a Power BI options-flow board (ORCL) the user wants to
reproduce from trading-intel data, and to know what's missing.*

## Bottom line

That board is **~80% reproducible from data you already collect.** The market-wide
options **tape** (`tas_prints`, which carries the *real aggressor side*) plus the
**greeks/GEX** layer already cover the net-premium, call/put, buy/sell,
accumulation, DTE-bucket, and magnet panels. Three panels need data that is **not
in the fixed vendor set** (Convex / CVForge / FMP) — equity **darkpool volume**,
**MOC/MOO auction imbalance**, and **Reddit/social sentiment** — each a new vendor
or source, so each needs an ADR (rule 1).

The caption's problem — *"I assume it's a buyer, but I can't say for sure"* — you've
already solved: `tas_prints.side` is the aggressor side straight off the tape, so
you can **say** buy/sell, not infer it from ΔOI (your `oi-flow-direction` rule).

## Panel → source → status

| Power BI panel | trading-intel source | Status |
|---|---|---|
| **Net Option Premium $Flow** (Call-Buy / Put-Sell / Put-Buy / Call-Sell) | `tas_prints` grouped by **cp × side**, `notional` | **DERIVABLE** — the 4-way cross isn't stored yet (`tas_daily_flow` keeps call/put and buy/sell *separately*); one small add to `flow/aggregate.py` |
| Call/Put & Buy/Sell notional, net premium, %buy, dominant side | `tas_daily_flow` (`call_notional`, `put_notional`, `buy_notional`, `sell_notional`, `net_premium_call_put`, `pct_buy`, `dominant_side`) | **HAVE** |
| Large-Inst / Medium / Retail premium split | `tas_prints.size` / `notional` bucketed by trade size | **DERIVABLE** — define size cutoffs; small add |
| DTE buckets (<7 / 7–31 / 1–3mo / >3mo) $flow | `tas_prints.expiry` → DTE, grouped | **DERIVABLE** |
| Accumulation / distribution (per name + per strike) | `flow/aggregate.py`, `tas_daily_flow`, `tas_daily_contract` (`dominant_side`, `net_dollar_delta`), `flow/scorecard.py` (accum_score) | **HAVE** — `get_flow_scorecard`, `get_flow_report` |
| Options table: which strikes bought/sold, "unusual" | `tas_daily_contract` (buy/sell prints per strike) + `get_time_and_sales` | **HAVE** |
| Urgency: **Sweep**, **Vol > OI** | Sweep: `spread_leg` flag + `options_flow.detect_structures` (packages); Vol>OI: `dashboard.watchlist_metrics.vol_oi_ratio` + `oi_chain_eod` / `greeks_chain` OI | **DERIVABLE** — a dedicated *sweep* classifier (aggressive same-side prints across strikes in a tight window) is a small add |
| **VEX/GEX/OI magnet** heatmap ("GEX good for direction") | `live_gex`, `gex_rolling`, `gex_term`, walls — `get_live_gex`, `get_gex_term`, `get_walls` | **HAVE** |
| Large-inst volume delta / raw / rollup | `tas_daily_flow` + per-print size | **HAVE** |
| Very-short-DTE (0/1DTE) intraday flow | `intraday_flow`, `delta_flow` — `get_intraday_flow`, `get_delta_flow` | **HAVE** |
| **Institutional Darkpool Vol** ($ / shares) | — none — | **GAP** (new vendor) |
| **MOC / MOO $ flow** (auction imbalance) | — none — | **GAP** (new vendor) |
| **Reddit Sentiment** | — none (the word only appears in LLM prompt text) — | **GAP** (new source) |
| "Phantom" | unknown proprietary metric | **UNMAPPABLE** without its definition |

## The view you can build now (all from tape + greeks)

A "Flow Intelligence" report/page, per name or market-wide:

1. **Net premium 4-way** — Call-Buy / Put-Sell / Put-Buy / Call-Sell gauges (the
   board's signature) = `tas_prints` grouped by cp × side.
2. **Who's trading** — large-inst / medium / retail premium split (by print size),
   %buy, dominant side.
3. **Where** — accumulation/distribution by strike (`tas_daily_contract`),
   DTE-bucket premium, top unusual prints (Vol > OI).
4. **Positioning context** — VEX/GEX/OI walls + magnet beside the flow (exactly the
   board's "GEX Added good for Direction" panel).
5. **Urgency** — sweep-flagged aggressive prints.

All descriptive (FlashAlpha rule 4): a *view*, not a signal.

## Data gaps + how to close each (each = an ADR, rule 1)

1. **Equity darkpool volume** — not in Convex/CVForge/FMP. Needs an off-exchange /
   FINRA-ATS print feed (a darkpool-data vendor). New vendor → ADR. Highest-cost gap.
2. **MOC/MOO auction imbalance** — the Nasdaq/NYSE closing/opening imbalance feed.
   Not in the current set. New vendor → ADR. (The tape captures option prints near
   the close, but not the equity auction imbalance.)
3. **Reddit / social sentiment** — buildable **cheaply without a paid vendor**:
   Reddit API (free tier) → local-Ollama sentiment (rule 7), banked like your other
   features. Still net-new (ADR for the new data domain), but $0.
4. **"Phantom"** — proprietary to that board; not mappable without its definition.

## Suggested first build

The **net-premium 4-way + inst/retail split + accumulation** report from
`tas_prints`. It's the board's core, needs only a small `cp × side` + size-bucket
addition to `flow/aggregate.py`, reuses `flow/` + the GEX layer, and runs off data
you already bank on the NAS. Deliverable options: an HTML flow report (like
`scripts/flow_report.py`) or a Streamlit dashboard page.
