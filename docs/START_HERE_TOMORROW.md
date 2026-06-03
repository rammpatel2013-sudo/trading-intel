# START HERE — Wed 2026-06-03

Pick up the TAS (options time-and-sales) flow pipeline. Everything below is for
**you** to run. PowerShell note: chain commands with `;` (not `&&`).

Status going in: MCP full-table coverage + Phase 2 analyzer + Phase 3 collector
are **built, tested, committed**. The `tas_prints` table is **live on the NAS**
(alembic head `0024`). The only thing not yet proven is a **live tape capture** —
last night's run hit 0 prints because it was after the 4pm close (the tape is
live-only). So today is: capture during market hours, verify, then deploy to NAS.

---

## 0. First thing (2 min) — commit the leftover config wiring

The `.env` knobs were wired into `Settings` after the last commit, so:

```
pytest -q
git add -A ; git commit -m "TAS: wire TAS_MIN_PREMIUM/TAS_LIMIT/TAS_RETENTION_DAYS settings"
```

`pytest -q` must be green before committing.

---

## 1. Capture the tape — DURING market hours (09:30–16:00 ET)

Double-click `run_tas_capture.bat` (or `python scripts/tas_capture.py`). Start it
near the open so it captures the full session; it auto-stops at 16:00 ET. You'll
see `poll N @ HH:MM:SS ET: +X kept` lines.

**Verify ~30 min in:** open `data\tas\2026-06-03.csv`. Confirm `size` and
`notional` are non-zero and `side` shows buy/sell (not the zeroed after-hours
shape). Paste ~10 rows to Claude to confirm the tape is real.

## 2. Build the analysis workbook

```
python scripts/tas_analyze.py --with-gex
```

Opens `data\tas\analysis_2026-06-03.xlsx`. Look at the **Unusual Rank** and
**Combos** tabs and flag anything that looks wrong — that's how we tune the
scoring weights.

## 3. Prove the NAS collector path (during RTH)

```
python -m trading_intel.scheduler.jobs.tas_capture_job
```

This runs one forced poll and writes to `tas_prints` on the NAS. Then have Claude
check `tas_prints` for fresh rows (or query it yourself).

## 4. Deploy to the NAS (after step 3 looks right)

- The migration is **already applied** to the NAS (head 0024) — skip it.
- Rebuild the NAS image `--no-cache` (so the new job code is in the image).
- Add two DSM Task Scheduler tasks (user: root), per `docs/NAS_TASKS.md`:
  - `tas_capture_job` — every minute, 09:30–16:00, Mon–Fri.
  - `prune_tas_prints` — daily 02:40.
  Command pattern: `bash .../scripts/nas/run_job.sh tas_capture_job`

---

## Optional `.env` knobs (defaults shown — only add a line to override)

These are documented in `.env.template`. They are **optional**; the defaults are
already baked in. Add to your local `.env` only if you want different values:

```
TAS_MIN_PREMIUM=25000      # keep prints with notional (price*size*100) >= this $
TAS_LIMIT=500              # prints pulled per poll
TAS_RETENTION_DAYS=30      # prune raw tas_prints older than this many days
```

- Raise `TAS_MIN_PREMIUM` (e.g. 50000) to capture only whales / a quieter table.
- Raise `TAS_LIMIT` if the tape is busy and a 1-min poll might miss prints.
- Lower `TAS_RETENTION_DAYS` to keep the raw table smaller (summaries kept long-term).

---

## After today: the last Phase 3 piece

Once a real day of `tas_prints` has accumulated, build (against real data so the
scoring is tuned to the actual tape):

1. An EOD `tas_daily_summary` roll-up table + job (per-ticker premium, net
   delta-flow, unusual score — kept long-term while raw prints prune at 30 days).
2. A `get_unusual_flow` MCP tool reading that summary, so the captured tape is
   queryable from Claude Desktop like every other table.

Any scored *alerts* off this (vs. a ranked watchlist) move to `strategies/` per
FlashAlpha rule 4.
