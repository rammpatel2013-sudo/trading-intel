# EM-break / Gamma Burn-off System — build + deploy handoff

*Built 2026-07-18. Implements the McGraw "post-earnings expected-move break → front-expiry gamma burn-off → post-OPEX defined-risk upside re-entry" pattern (concept: `docs/learning/em-break-gamma-burnoff-digest.md`). Two descriptor layers — a single-name event spine and an index-level systematic-flow engine — converge on one signal-eligible scanner.*

## What shipped

**Pure descriptor cores (unit-tested, 32 new tests green):**

- `trading_intel/earnings/em_break.py` — expected-move-break trigger: `em_break()` (break ratio / σ / direction) + `over_realization()` (still extending vs retracing). Tests: `tests/earnings/test_em_break.py`.
- `trading_intel/greeks/gamma_burnoff.py` — `front_dte_share()`, `phase()` (mechanical/transition/linear), `burnoff_state()` (+ decay + OPEX countdown). Tests: `tests/greeks/test_gamma_burnoff.py`.
- `trading_intel/flows/` — systematic-flow stack. `registry.py` (vol-control / CTA / risk-parity cohort AUM + target-vol assumptions), `descriptors.py` (inverse-vol exposure + convexity, `cohort_flow`, `aggregate_systematic_buying`, `overwriter_call_supply`). Tests: `tests/flows/test_descriptors.py`.
- `trading_intel/strategies/em_break_reentry.py` — `evaluate_reentry()` pure gate (0-100 conviction, prerequisites) + `emit_signals()` (writes `EM_BREAK_REENTRY`, `experimental=True`). Tests: `tests/strategies/test_em_break_reentry.py`.
- `trading_intel/clients/earnings_parse.py` — defensive `earn_cal` JSON → `EarningsDate`. Tests: `tests/clients/test_earnings_parse.py`.

**Anchor plumbing (P1):**

- `memory/models.py` — added `UniqueConstraint("symbol","date")` to `EarningsEvent` + new `PreEarningsStraddle` model.
- `alembic/versions/0037_earnings_anchor.py` — creates `pre_earnings_straddle`, adds the `earnings_events` unique constraint. Reversible.
- `clients/__init__.py` — `EarningsDate` value object + `EarningsCalendarSource` Protocol.
- `clients/convex_app.py` — `upcoming_earnings()` typed wrapper over `earn_cal` (no new vendor, rule 1).

**Collectors + scanner jobs:**

- `scheduler/jobs/earnings_calendar.py` — `earn_cal` → `earnings_events` (idempotent, daily).
- `scheduler/jobs/pre_earnings_straddle.py` — pre-earnings ATM straddle baseline for watchlist names with an upcoming print → `pre_earnings_straddle`.
- `scheduler/jobs/em_break_reentry.py` — assembles features → `emit_signals`.

**MCP reader tools** (`mcp/em_tools.py`, registered in `mcp/server.py`): `get_earnings_calendar`, `get_em_break`, `get_gamma_burnoff`, `get_vol_control_flow`, `get_systematic_flow`.

**Config** (`config.py`): `EARNINGS_LOOKAHEAD_DAYS`, `PRE_EARNINGS_SNAP_DAYS`, `PRE_EARNINGS_TARGET_DTE`, `EM_BREAK_LOOKBACK_SESSIONS`, `VOL_CONTROL_INDEX`, `VOL_CONTROL_AUM`, `VOL_TARGET`, `CTA_AUM`, `RISK_PARITY_AUM`, `RV_ROLLOFF_HORIZON`.

**Local wiring** (`scheduler/runner.py`): three new jobs registered — `earnings_calendar` (06:30 ET), `pre_earnings_straddle` (06:50 ET mon-fri), `em_break_reentry` (17:00 ET mon-fri).

Rule compliance: all upstream modules are descriptors (rule 4); only `strategies/em_break_reentry.py` writes to `signals`, and only `experimental=True` until backtested. No new vendor (`earn_cal` is the existing ConvexValue pro login — rule 1). Jobs are idempotent upserts (rule 5). Migration reversible (rule 3).

## Verification done

`pytest` on the 5 new suites = **32 passed**. Every new/edited module `py_compile`s and imports cleanly (`em_tools`, all three jobs, `flows`, `earnings`, `strategies`, `clients.convex_app`). Not runtime-tested against Postgres/Convex (no DB/vendor in the build env) — see deploy steps.

## ▶ Deploy steps (in order)

1. **~~Confirm the `earn_cal` schema.~~ ✅ CONFIRMED 2026-07-18** — live header is `[date, symbol, eps, eps_estimated, time, revenue, revenue_estimated, fiscal_date_ending, updated_from_date]`; the parser picks the ISO `date`, `symbol`, and `time` ("bmo"/"amc") columns correctly. Locked by a real-schema case in `tests/clients/test_earnings_parse.py`. No change needed.
2. **Migrate:** `alembic upgrade head` (laptop + NAS DB — one shared Postgres per `db-topology`). Round-trip check: `alembic downgrade -1 && alembic upgrade head`.
3. **Backfill/seed:** run `python -m trading_intel.scheduler.jobs.earnings_calendar` then `python -m trading_intel.scheduler.jobs.pre_earnings_straddle` once so baselines start banking (the pre-earnings straddle only exists going forward — it can't be reconstructed for past prints; chain history is thin, so this system **banks forward**).
4. **NAS DSM tasks** (image rebuild `--no-cache` first — code changes don't take effect until the baked image is rebuilt; git isn't on the NAS, update via GitHub tarball). Add three DSM Task Scheduler tasks calling `docker run … python -m trading_intel.scheduler.jobs.<X>`: `earnings_calendar` 06:30, `pre_earnings_straddle` 06:50 (mon-fri), `em_break_reentry` 17:05 (after gex_rolling/oi_chain/quotes). Batch with the other pending NAS deploys (factor_scores, frawd, skew-backfill, iv_term, surface).
5. **Restart Claude Desktop** to re-register the 5 new MCP tools.

## Enrichment TODOs (documented, non-blocking)

The re-entry job (`em_break_reentry.py`) leaves three optional features `None`; the gate degrades gracefully. Wire when convenient: `straddle_label` from `get_straddle` decay (`straddle_decay` already returns `decaying/repricing_up/flat`), `vrp_normalizing` from `get_vol_richness`, and per-name `overwriter_rebuilding` via ΔOI+ΔIV pairing (`get_oi_changes`). Also: `get_gamma_burnoff` phase currently uses the front-share proxy — layer the spot-ladder `gamma_profile` gamma-at-spot/peak for the sharper mechanical→linear read.

## Assumptions to calibrate (systematic-flow $)

`flows/registry.py` cohort AUM (vol-control ~$350B, CTA ~$300B, RP ~$150B) and target-vol (~10%) are **estimates** — pull current desk figures (Nomura/McElligott, GS, DB) and update the registry / `Settings`. Until then, **consume the flow $ as a percentile / sign, not a hard number** (the tools carry that caveat). Index universe defaults to SPY/QQQ proxies (quotes coverage); SPX has no `quotes_daily` series.

## Backtest (P6, when ready)

Validate `EM_BREAK_REENTRY` before removing `experimental`: historical earnings gaps vs pre-earnings straddle (banked forward from step 3), front-gamma burn-off through OPEX, and the re-entry return to the call wall. Interim, rank cross-sectionally rather than a deep historical fit (chain history is thin; `no-ibkr-api` percentile pattern).

## Related

`docs/decisions/ADR-006-earnings-anchor.md`, `docs/decisions/ADR-007-systematic-flow-proxy.md`, `docs/playbooks/em_break_reentry.md`, `docs/learning/em-break-gamma-burnoff-digest.md`, `docs/learning/vs3d-dealer-exposure-digest.md`.
