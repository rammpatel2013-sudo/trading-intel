# Runbook — restore skew + index/SPX collection

_Authored 2026-06-23. Fixes the four data-collection gaps found this week._

## The problems (and why, in dependency order)

| # | Symptom | Root cause | Fixable? |
|---|---------|-----------|----------|
| 1 | `oi_chain_eod` has **no SPX** since **6/3** (single-names current to 6/22) | SPX-specific fetch failing or SPX dropped from the universe; job logs `oi_chain_eod.symbol_failed symbol=SPX` and continues | Forward only (option chains are vendor snapshots — history not re-pullable) |
| 2 | `vix_options_chain` stuck at **5/28** | `vix_options` job not running on the NAS (no DSM task) or failing | Forward only |
| 3 | `index_skew_daily` stuck at **6/12** though the task runs daily | `index_skew` throws because its inputs (SPX surface #1, VIX options #2) are dead, **and** it may be firing before those inputs are collected (ordering) | Partial backfill (VOLI/TDEX/SDEX only); rest forward |
| 4 | per-name `skew_snapshots` has only **5/28** | Job never scheduled on the NAS (no DSM task) | Forward; history backfill needs a small code tweak (see Part D) |

Plus a **silent staleness bug**: `vol_regime` keeps emitting `INDEX_VOL_REGIME` signals dated today off the frozen 6/12 `index_skew_daily` row.

**Fix order is upstream-first:** 1 & 2 (feeds) → 3 (index_skew) → 4 (per-name skew) → verify. 3 and 4 heal on their own once their inputs and schedules are right.

Canonical EOD schedule (from `scheduler/runner.py`, all ET):

```
16:30 gex_rolling      16:42 vix_options       16:50 index_skew
16:35 oi_chain_eod     16:44 vix_expirations   16:55 skew_snapshots
16:40 vol_richness     16:45 vix_snapshot      17:00 vol_regime
```

`index_skew` and `skew_snapshots` MUST run after `oi_chain_eod`, `vix_options`, `vix_snapshot`.

---

## Part A — Diagnose on the NAS (read-only, do this first)

SSH to the NAS as `drmithil`. Docker needs `sudo` here (DS923+, drmithil not in docker group).

1. **Read the failing-job logs** (`run_job.sh` writes one per job):
   ```bash
   tail -n 40 ~/ti_index_skew.log        # the exact exception index_skew dies on
   tail -n 40 ~/ti_vix_options.log       # if this file is MISSING -> task never existed
   ls -la ~/ti_skew_snapshots.log        # MISSING expected -> confirms no DSM task
   grep -i "symbol_failed.*SPX\|SPX" ~/ti_oi_chain_eod.log | tail
   ```
2. **List existing DSM tasks** to see what's actually scheduled: DSM → Control Panel → Task Scheduler. Note every task whose script ends in `run_job.sh ...` and its time.

Expected outcome: you confirm (a) `index_skew`'s traceback names a missing SPX/VIX input or an external fetch, (b) `vix_options` and `skew_snapshots` have no DSM task, (c) the `index_skew vol_regime` task fires at 16:30 (too early).

---

## Part B — Restore the upstream feeds (problems 1 & 2)

### B1. SPX in `oi_chain_eod`
Find why SPX stopped. From the **laptop** (repo + venv, `.env` → NAS), test the vendor fetch and the universe:
```bash
.venv\Scripts\python -c "from trading_intel.config import get_settings; from trading_intel.watchlist import effective_symbols; from trading_intel.memory.db import make_session_factory; s=get_settings(); sf=make_session_factory(s); ses=sf(); print('SPX in universe:', 'SPX' in effective_symbols(ses, s))"
.venv\Scripts\python -c "from trading_intel.clients.convex import ConvexClient; c=ConvexClient(); df=c.chain_long('SPX'); print('rows:', len(df))"
```
- If SPX is **not in the universe** → re-add it (watchlist/config) the same way other index symbols are added.
- If the fetch **errors/returns 0 rows** → it's a symbol-format/entitlement issue with Convex for SPX (try `SPXW`, `$SPX`, or check the Convex symbol). Fix in `clients/convex.py` behind the `OptionsDataSource` protocol (CLAUDE.md rule 1 — don't reach around it).

### B2. `vix_options`
If Part A showed no `~/ti_vix_options.log` / no DSM task, add one (Part C). If it exists and errors, read the traceback and fix the source the same protocol-respecting way.

> Note: the **5/28→now gap for SPX and vix_options is permanently lost** — vendor option chains are point-in-time snapshots, not history. Goal here is to resume **forward** collection.

---

## Part C — Add / fix the DSM tasks (problems 2 & 4, and ordering for 3)

Each DSM task is *User-defined script*, user **root**, that calls the one wrapper. Add these if missing, and **fix the time on the existing `index_skew vol_regime` task** so it runs after the feeds.

Recommended: one chained EOD task in canonical order (simplest, guarantees ordering):
```bash
bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_job.sh \
  gex_rolling oi_chain_eod vol_richness vix_options vix_expirations \
  vix_snapshot index_skew skew_snapshots vol_regime
```
Schedule it **once daily ~16:35 ET** (after the cash close + a few minutes for data). `run_job.sh` runs them in sequence, so ordering is correct by construction.

If you prefer separate tasks, keep these **relative times** (ET): `oi_chain_eod` 16:35, `vix_options` 16:42, `vix_snapshot` 16:45, `index_skew` 16:50, `skew_snapshots` 16:55, `vol_regime` 17:00 — and **move your current 4:30 `index_skew vol_regime` task to 16:50** (or delete it in favour of the chained task above).

Smoke-test any task immediately (don't wait for cron):
```bash
sudo bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_job.sh vix_options
sudo bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_job.sh index_skew skew_snapshots
tail -n 20 ~/ti_vix_options.log ~/ti_index_skew.log ~/ti_skew_snapshots.log
```

---

## Part D — Backfill what is backfillable

### D1. `index_skew_daily` (VOLI / TDEX / SDEX + percentiles) — script already exists
From the **laptop**:
```bash
.venv\Scripts\python scripts\backfill_index_skew.py --start 2026-06-13 --end 2026-06-23
```
This upserts only the Yahoo-sourced columns; it does **not** touch SPX-RR / VVIX / VIX-options / proxy columns (those need the SPX surface we don't have for those days). Idempotent.

### D2. per-name `skew_snapshots` history — needs a 1-line code tweak first
`build_rows()` is `as_of`-aware **except** `_latest_chain()`, which always takes the newest chain. To reconstruct history from the stored daily chains (5/23–6/22), make the chain lookup respect `as_of`:

- In `trading_intel/scheduler/jobs/skew_snapshots.py`, change `_latest_chain(session, symbol)` to accept `as_of` and filter `WHERE OiChainEod.ts::date <= as_of` before `ORDER BY ts DESC LIMIT 1`; pass `as_of` through from `build_rows`. (Small, reversible; add a test in `tests/`.)
- Then loop the job over each stored chain date (laptop):
  ```bash
  .venv\Scripts\python scripts\backfill_skew_snapshots.py --start 2026-05-23 --end 2026-06-22
  ```
  (Write `backfill_skew_snapshots.py` mirroring `backfill_index_skew.py`: for each date with an `oi_chain_eod` snapshot, call `skew_snapshots.build_rows(session, settings, as_of=d)` then upsert.)

If you'd rather not touch code now: skip the backfill and just let the daily job (Part C) accrue history forward — the 252d percentile simply needs ~time to populate.

---

## Part E — Stop `vol_regime` masking staleness (hardening, optional)

In `strategies/vol_regime.py`, guard the classifier so it only emits when **today's** `index_skew_daily` row exists; otherwise skip or emit a `STALE`/`NO_DATA` state instead of classifying off an old row. Prevents a future silent freeze from looking current.

---

## Part F — Verify (laptop, against the NAS DB)

After the next scheduled run (or the smoke-tests), these should all show **today**:
```sql
SELECT max(ts)::date FROM oi_chain_eod WHERE symbol='SPX';         -- SPX chain back
SELECT max(ts)::date FROM vix_options_chain;                       -- vix options back
SELECT max(date)     FROM index_skew_daily;                        -- index skew current
SELECT max(ts)::date, count(distinct symbol) FROM skew_snapshots;  -- per-name skew flowing
SELECT signal_type, max(ts)::date FROM signals
  WHERE signal_type ILIKE '%REGIME%' GROUP BY 1;                   -- regime now off fresh data
```
Then re-run the watchlist report and a ticker report — the skew panel should populate, and once `rr_25d_pctile_252d` is non-null the "price down / skew not spiking" read becomes usable.

---

## One-glance checklist
- [ ] A: read NAS logs, list DSM tasks
- [ ] B1: SPX back in `oi_chain_eod` (universe or Convex symbol fix)
- [ ] B2: `vix_options` running
- [ ] C: chained EOD DSM task in canonical order @ ~16:35; old 4:30 task removed/retimed
- [ ] D1: `backfill_index_skew.py` for 6/13–6/23
- [ ] D2 (optional): as-of chain tweak + `backfill_skew_snapshots.py`
- [ ] E (optional): vol_regime staleness guard
- [ ] F: verify all maxes = today
