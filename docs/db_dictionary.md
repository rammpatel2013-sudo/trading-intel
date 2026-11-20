# Database dictionary — trading-intel

Written 2026-11-19 (DB foundation wave A1) from the 2026-11-14 schema contract and the live-DB check
of 2026-11-15. `tests/test_db_dictionary.py` fails when an ORM table has no row here, or a row names
a table the ORM no longer has — add the row in the same ship as the migration.

Kind: raw · raw† = our math on a live pull not stored in full (can't rebuild — treat as raw) ·
derived (rebuildable) · LLM · report · retired (writer stopped, table kept for history).

## Rules for every NEW table / column

1. One owner table per concept — reuse it, add columns or a view; never copy data into a new table.
2. Natural key enforced as PK or UNIQUE (`NULLS NOT DISTINCT` when a key column can be NULL).
3. Writers upsert `ON CONFLICT` on the natural key, so jobs are re-runnable.
4. Daily time key = `date` (DATE, US session date, Eastern).
5. Intraday `ts` = naive Eastern wall-clock, written only via `timeutils.eastern_now()` /
   `eastern_today()`. Never `datetime.now()`, `utcnow()` or `date.today()` — NAS containers run UTC.
   `tests/test_clock_rules.py` enforces this.
6. `ingested_at` on new tables, Eastern, via `eastern_now()`.
7. Late/revised data keeps `available_date` — no look-ahead.
8. `symbol` uppercase, no prefix (`SPX`, not `^SPX`); option root in `root` (`SPXW`). Option
   contracts are keyed by the 21-char OCC symbol; vendor ids are stored beside it (A2).
9. `source` column wherever more than one vendor can fill a table.
10. Units in column names when not obvious (`_usd`, `_pct`, `_shares`); IV as decimal;
    + gamma = dealers long.
11. Raw is never dropped.
12. Alembic only (hand-written, never `--autogenerate`), one migration per ship, working downgrade.
    After each migration ship run `python scripts/schema_drift.py` (report only).

Every scheduled job run is logged to `pipeline_runs` by `python -m trading_intel.scheduler.track <job>`
(what `scripts/nas/run_job.sh` calls).

Known type drift (report 2026-11-19, not fixed — renaming/retyping costs more than it buys):
`greeks_chain.ts`, `greeks_snapshots.ts`, `signals.ts`, `documents.ingested_at` are `timestamptz`
in the DB but naive in the ORM; the server TimeZone is UTC. `documents.ingested_at` now writes an
aware UTC instant. `themes.scope` is VARCHAR in the DB, Enum in the ORM.

## Tables (81)

### Options chains and greeks

| Table | One row = | Key | Time key | Writer | Source | Kind |
| --- | --- | --- | --- | --- | --- | --- |
| `oi_chain_eod` | contract per day (~180 DTE wide) | symbol+ts+source+expiry+strike+cp | `ts` timestamp | oi_chain_eod | Convex | raw; kept 365 days |
| `oi_chain_daily` | symbol per day: OI, volume, walls, ATM IV | symbol+ts | `ts` date | oi_chain_daily | from oi_chain_eod | derived |
| `greeks_chain` | contract per snapshot | symbol+ts+source+expiry+strike+cp | `ts` timestamptz | chain_snapshot, live_gex | Convex | raw; no prune yet (13 GB) |
| `greeks_snapshots` | symbol per snapshot: GEX/DEX/VEX/CHEX totals | symbol+ts+source | `ts` timestamptz | greeks_snapshot, index_greeks_snapshot, sector_greeks | Convex; CVForge for sector ETFs | raw† |
| `live_gex` | near-money contract per intraday refresh | symbol+ts+strike+cp+expiry | `ts` timestamp | live_gex | Convex | raw; pruned nightly |
| `intraday_flow` | 0DTE/1DTE contract per 5-min cycle | symbol+ts+source+expiry+strike+cp | `ts` timestamp | intraday_flow | Convex | raw; pruned |
| `gex_rolling` | symbol per day: total GEX to ~180 days | symbol+ts+source | `ts` timestamp | gex_rolling | Convex | raw† |
| `gex_term` | expiry per gex_rolling row | symbol+ts+source+expiration | `ts` timestamp | gex_rolling | Convex | raw† |
| `surface_snapshots` | listed strike per expiry per day | symbol+ts+expiry_date+strike | `ts` date | surface_snapshots | Convex | raw |
| `vix_options_chain` | VIX option contract per day | ts+expiration+strike+opt_kind | `ts` date | vix_options | Convex | raw |
| `pre_earnings_straddle` | symbol per earnings date: ATM straddle | symbol+earnings_date | `earnings_date` | pre_earnings_straddle | Convex | raw† |
| `equity_call_iv` | symbol per day: call IV for the trade card | date+symbol | `date` | equity_daily | CVForge | raw† |
| `desk_gamma_daily` | symbol per day: short-term vs total gamma | date+symbol | `date` | desk_brief | CVForge | raw† |
| `sector_snapshots` | sector ETF per day: RR, walls, IV grid | as_of+symbol+source | `as_of` date | sector_greeks | CVForge | raw† |

### Option flow and tape

| Table | One row = | Key | Time key | Writer | Source | Kind |
| --- | --- | --- | --- | --- | --- | --- |
| `tas_prints` | one large option print | ts+symbol+price+size+source (NULLS NOT DISTINCT) | `ts` naive ET, `trade_date` | tas_capture_daemon | Convex tas | raw; prune pending |
| `tas_crowd_minute` | SPX minute × contract × size bucket | trade_date+minute+root+expiry+strike+cp+bucket | `trade_date`, `minute` | tas_capture_daemon | Convex tas | raw (summed) |
| `tas_daily_flow` | root per day: buy/sell premium, $ delta | trade_date+root | `trade_date` | tas_daily_rollup | from tas_prints | derived |
| `tas_daily_contract` | contract per day | trade_date+root+expiry+strike+cp (NULLS NOT DISTINCT) | `trade_date` | tas_daily_rollup | from tas_prints | derived |
| `flow_snapshots` | symbol per snapshot: premium, P/C tilt | symbol+ts+source | `ts` timestamp | flow_snapshot | Convex | raw† |
| `delta_flow` | symbol per 5-min: cumulative $ delta | symbol+ts+source | `ts` timestamp | none (retired 2026-11-19) | Convex | retired; last row 6/04 |
| `desk_und_daily` | symbol per day: option volume split | date+symbol | `date` | desk_snapshot | Convex app | raw |
| `pos_days` | contract per day: customer buy/sell volume | symbol+trade_date+expiry+strike+right | `trade_date` | scripts/cvxmap/pos_ledger.py | Convex raw NDJSON | derived |
| `pos_ledger` | contract per morning: running signed position | symbol+as_of_date+expiry+strike+right | `as_of_date` | scripts/cvxmap/pos_ledger.py | from pos_days | derived |
| `flow_und_daily` | symbol per day: option flow, ~50 fields (buy/sell $ / contracts / delta / gamma / vega, call/put, ratios, no-side volume, share day_volume) | date+symbol | `date` | flow_bank 16:25 | Convex und screener + index rows by name | raw |
| `flowchart_15s` | symbol per 15 s: flownet, vflownet, call/put bought-sold, call/put gxoi/dxoi/vxoi/txoi, prop1-4 | symbol+ts | `ts` naive ET, `trade_date` | flow_bank 16:25 | Convex flowchart (today only) | raw |
| `od_flow_daily` | symbol × expiration (NULL = all) × right × category (regular/spread/eth/eth_spread) × side (buy/sell/undecided) | date+symbol+expiration+right+category+side (NULLS NOT DISTINCT) | `date` | flow_bank --steps od_flow 17:25 | optionsdata.ai files from od_nightly | raw |

### Vol, skew and VIX

| Table | One row = | Key | Time key | Writer | Source | Kind |
| --- | --- | --- | --- | --- | --- | --- |
| `iv_tenor_snapshots` | symbol × tenor per day: constant-maturity IV | symbol+ts+tenor_dte | `ts` date | iv_tenor_snapshots | Convex | raw† |
| `vol_surface_cm` | symbol × day × tenor × delta × wing | symbol+ts+dte+delta+side | `ts` date | vol_surface_cm | Convex | raw† |
| `skew_snapshots` | symbol × day × horizon: RR, fly | symbol+ts+horizon_dte | `ts` date | skew_snapshots | stored chain | derived |
| `vol_richness` | symbol × day × horizon: IV vs RV | symbol+ts+horizon_dte | `ts` date | vol_richness | stored data | derived |
| `index_skew_daily` | one day: SKEW, SDEX, RR, VVIX, tail score | date | `date` | index_skew | Cboe + own | raw + derived |
| `vix_data` | one day: VIX term, VVIX, credit, MOVE | date | `date` | vix_snapshot | Cboe, FRED, FMP via CVForge | raw |
| `vix_expirations` | VIX monthly settlement date | expiration | `expiration` | vix_expirations | calendar math | derived |
| `vix_signal_snapshots` | one day: VIX overshoot vs SPX move | date | `date` | vol_tab | own | derived |
| `straddle_snapshots` | symbol per day: 0DTE straddle % of spot | symbol+date | `date` | vol_tab | replay / map_daily | derived |
| `squeezemetrics_daily` | one day: DIX + GEX | date | `date` | squeezemetrics | SqueezeMetrics | raw |

### Map, dealer state, signals, jobs

| Table | One row = | Key | Time key | Writer | Source | Kind |
| --- | --- | --- | --- | --- | --- | --- |
| `map_daily` | symbol per session: MAP card + grade | symbol+date | `date` | map_store, map_grade | own | derived |
| `gex_transition_daily` | symbol per day: gamma transition | symbol+ts | `ts` date | gex_transition | own | derived |
| `beta_reset_daily` | one day: low-beta break × high-beta hold | date | `date` | beta_reset | FMP bars | derived |
| `signals` | one triggered signal | none (2 per day is legit for some types) | `ts` timestamptz | strategies/* only | own | derived |
| `signal_outcomes` | signal: target/stop hit, R | signal_id | — | em_break_validation | own | derived |
| `pipeline_runs` | one scheduled job run: status, rows, error | id | `run_date`, `started_at` naive ET | scheduler/track.py | own | raw (ops) |

### Prices, breadth and market

| Table | One row = | Key | Time key | Writer | Source | Kind |
| --- | --- | --- | --- | --- | --- | --- |
| `equity_bars` | symbol per day: OHLCV + cap (S&P 1500) | symbol+date | `date` | equity_daily, equity_backfill | FMP via CVForge | raw — owner of daily bars |
| `desk_bars` | symbol per day: OHLCV (outside S&P 1500) | symbol+date | `date` | desk_brief | FMP via CVForge | raw — duplicate (A2: view) |
| `quotes_daily` | symbol per day: OHLCV + RV20/60 | symbol+date | `date` | quotes_daily | FMP via CVForge | raw — duplicate (A2: view) |
| `breadth_snapshots` | one day: A/D, %>MA, NH-NL, McClellan | ts+source | `ts` date | breadth | own | derived |
| `external_nhnl` | one day: new highs/lows | date | `date` | equity_daily | thetrading.tools | raw |
| `sector_corr_snapshots` | one day: sector-ETF correlation | as_of+source | `as_of` | sector_corr | FMP bars | derived |
| `finra_short_volume` | symbol per day: short volume | date+symbol | `date` | finra_dix, desk_backfill | FINRA | raw |
| `finra_dix_daily` | one day: our DIX | date | `date` | finra_dix | own | derived |
| `short_interest_snapshots` | symbol per day: SVR, settled SI | symbol+ts+source | `ts` date | short_interest_snapshots | FINRA | raw |
| `bars_1m` | symbol per minute: OHLCV (ETFs) | symbol+ts+source | `ts` naive ET (bar start) | flow_bank 16:25 | FMP historical-chart/1min via CVForge | raw |
| `letf_shares_snapshots` | LETF per day: shares outstanding | symbol+ts | `ts` date | letf_flows (CVForge since 2026-11-19) | FMP via CVForge | raw |

### Equity swing side

| Table | One row = | Key | Time key | Writer | Source | Kind |
| --- | --- | --- | --- | --- | --- | --- |
| `equity_members` | one index-membership interval | symbol+index_code+start_date (NULLS NOT DISTINCT) | — | equity_weekly | FMP via CVForge | raw |
| `equity_profiles` | one company profile | symbol | — | equity_weekly | FMP via CVForge | raw |
| `equity_fundamentals` | fiscal quarter per symbol | symbol+period_end | `period_end` | equity_weekly | FMP via CVForge | raw — owner |
| `equity_factor_returns` | factor per day | date+factor | `date` | equity_daily | own | derived |
| `equity_stock_factors` | symbol per day: exposures | date+symbol | `date` | equity_daily | own | derived |
| `equity_gate_daily` | one day: market gate | date | `date` | equity_daily | own | derived |
| `equity_candidates` | symbol × list per day | date+symbol+list_name | `date` | equity_daily | own | derived |
| `fundamentals_snapshots` | symbol per week: ratios + z-scores | symbol+ts | `ts` date | factor_scores | FMP via CVForge | raw + derived |
| `sentiment_snapshots` | symbol per week: 13F, ratings, targets | symbol+ts | `ts` date | sentiment | FMP via CVForge | raw |
| `estimate_snapshots` | symbol per week: next EPS/revenue est. | symbol+ts | `ts` date | estimate_snapshots | FMP via CVForge | raw |
| `swing_features` | symbol per day: feature vector | symbol+ts | `ts` date | scripts/swing_features.py | CVForge | raw† |
| `earnings_events` | symbol per earnings date | symbol+date | `date` | earnings_calendar | Convex app | raw |
| `kpi_snapshots` | symbol per quarter: call KPIs | symbol+period_label | `ts` date | kpi_snapshots | FMP + Ollama | LLM |
| `desk_names` | one optionable name | symbol | — | desk_brief | FMP | raw |
| `desk_picks` | date × source × symbol × role | date+source+symbol+role | `date` | desk_brief, MCP | own + posted | derived + manual |
| `desk_brief_daily` | one day: desk brief | date | `date` | desk_brief | own | report |

### Research, text and LLM output

| Table | One row = | Key | Time key | Writer | Source | Kind |
| --- | --- | --- | --- | --- | --- | --- |
| `tickers` | one watchlist symbol | symbol | — | watchlist scripts | .env + ingest | manual |
| `research_notes` | symbol per day: note; letter bodies | symbol+as_of | `as_of` | research_notes, letters_fetch | EDGAR, FMP, Gmail + Ollama | LLM |
| `surface_reports` | symbol per day: narrative | symbol+as_of | `as_of` | none (retired 2026-11-19) | Ollama | retired |
| `am_summaries` | one day: AM summary | date | `date` | none (retired 2026-11-19) | Ollama | retired |
| `newsletter_levels` | source × day × level | source+as_of+name | `as_of` | letters_fetch | Gmail + Ollama | LLM |
| `newsletter_scenarios` | source × day × scenario | source+as_of+idx | `as_of` | letters_fetch | Gmail + Ollama | LLM |
| `documents` | one ingested document | sha256 | `ingested_at` timestamptz | pdf_pipeline, filings_fetch | PDFs, EDGAR | raw |
| `chunks` | one text chunk + embedding | document_id+chunk_idx | `date` | pdf_pipeline, sync_knowledge | documents | derived |
| `watchlist_entries` | symbol per source doc | symbol+source_doc_id (NULLS NOT DISTINCT) | — | research ingest | PDFs + Ollama | LLM |
| `filing_holdings` | fund × quarter × CUSIP | cik+period+cusip | — | filings_fetch | FMP, EDGAR | raw |
| `themes` | one theme name | name | — | research ingest | Ollama | LLM |
| `theme_observations` | one theme sighting | none (re-sightings are kept) | `date` | research ingest | Ollama | LLM |

Dropped 2026-11-19 (migration 0059): `flow_buckets`, `alerts_sent` — never written, 0 rows.
