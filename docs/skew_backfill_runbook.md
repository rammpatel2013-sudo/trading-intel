# Skew history backfill + keep-current — runbook

**Goal:** load 2 years of per-name 25Δ skew (from `skew_data.xlsx`) into `skew_snapshots`
so `get_skew_history` and the Skew dashboard page have a real 252-day percentile
baseline — then let the existing daily job keep it current. Closes the long-open
"skew not collected" item.

The importer (`scripts/skew_backfill.py`) reuses the repo's own `SkewSnapshot` model,
session factory, and `vol/skew.py` helpers, so its rows match the daily job's schema,
units, and upsert key `(symbol, ts, horizon_dte)` exactly. It writes **horizon 30d**
rows (the workbook is a single 30d constant-maturity tenor); the daily job fills
60/90/180/365 forward and recomputes the 63d/252d percentiles off this baseline.

---

## 1 · One-time backfill — run on the LAPTOP
The laptop `.env` already points at the NAS Postgres (see `db-topology`), so running
here writes straight to prod. No NAS SSH needed for the backfill.

```powershell
cd C:\Users\drmit\PycharmProjects\trading-intel
.venv\Scripts\activate
python scripts\skew_backfill.py --file skew_data.xlsx --dry-run
```

The dry run prints importable symbols + per-symbol row counts and lists any names it
skips. **SPX/QQQ are skipped by design** — they aren't in the `tickers` table (index
ETFs live in `iv_tenor_snapshots`, read via `get_iv_tenor`). SMH/IWM import only if
they're watchlist tickers. If the preview looks right:

```powershell
python scripts\skew_backfill.py --file skew_data.xlsx --through 2026-07-09
```

- `--through 2026-07-09` lets today's 16:55 job own 7/10 onward (avoids the partial
  rows where META/AVGO/DELL are missing today's call side).
- Idempotent (`ON CONFLICT … DO UPDATE`) — safe to re-run.
- Expect ~6.7k rows across the single-name sheets.

## 2 · Verify
```sql
SELECT max(ts) AS latest, count(DISTINCT symbol) AS names
FROM skew_snapshots WHERE horizon_dte = 30;
```
Or just ask me "skew history for AAPL" — `get_skew_history` should now return ~500
days with a real 252d percentile instead of the old two-row stub.

## 3 · Keep it current — the daily DSM task (on the NAS)
This is the forward collector that was never scheduled (per
`docs/SETUP_skew_snapshots_dsm_task.md`):

- NAS → Control Panel → Task Scheduler → Create → Scheduled Task → **User-defined
  script**, user **root**.
- Command: `bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_job.sh skew_snapshots`
- Schedule: **Daily 16:55 ET** (after the 16:35 `oi_chain_eod` load; NAS clock is US/Eastern).
- Smoke-test first: `sudo bash …/run_job.sh skew_snapshots` then
  `tail -n 30 ~/ti_skew_snapshots.log` — look for `skew_snapshots.done rows=… symbols=…`
  and `EXIT 0`. If you see `No module named …skew_snapshots`, rebuild the image
  `--no-cache` (deployed image predates the collector).

---

## Notes / caveats
- **Dropped on load:** degenerate fits (18 QQQ rows where a wing IV blew up past the
  ±25 vol-pt / 300% sanity bounds), rows missing a wing or ATM (e.g. META/AVGO/DELL's
  partial 7/10 call side), and any row the workbook marked `excluded`.
- **Left NULL by the backfill** (the daily job fills these forward): `rr_10d`, `bf_10d`,
  `front_back_rr_slope`, `vix_beta_60d`, `rr_25d_abnormal`.
- **Methodology seam:** backfilled history uses the workbook's term-interpolated 30d
  IVs; forward rows use the `oi_chain_eod` nearest-expiry delta surface. Fine for a
  percentile baseline — just don't expect the handoff day to agree to the basis point.
- **Index ETFs:** if you also want SPX/QQQ skew *history* (not just forward via
  `iv_tenor_snapshots`), say so — that's a small second mapping into `iv_tenor_snapshots`
  (different columns: `iv_put_25d` / `iv_call_25d` / `iv_atm` / `tenor_dte`).
