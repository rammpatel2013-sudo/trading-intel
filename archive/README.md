# archive/ — unused code and stale plans (moved here 2026-11-14, O4 of the code audit)

Nothing here is imported, built into the image, tested or run. Files keep their old path under `archive/`
so `git log --follow` still works and a restore is one `git mv` back.

| Was | Why it is here |
|---|---|
| `trading_intel/dashboard/pages/` (20 pages) + `trading_intel/dashboard/Home.py` | Streamlit UI, unused since 10/28 (the cockpit + reports + MCP tools replaced it). The data loaders it used (`trading_intel/dashboard/*_data.py`, `*_view.py`, ...) **stay** in the package: MCP tools, jobs and reports import them. |
| `trading_intel/api/app.py` + `api/routes/` + `api/static/cockpit.html` | FastAPI cockpit service, never started on the NAS. `trading_intel/api/*.py` (positioning, sector, breadth, ...) **stay**: the MCP tools use them. |
| `docker-compose.prod.yml`, `.github/workflows/deploy.yml` | DigitalOcean droplet deploy, never used. Production is the NAS (`scripts/nas/`, DSM tasks). |
| `check_*.py`, `probe_*.py`, `db_freshness.py` (repo root) | One-off probes / checks from June-July. |
| `MASTER_PLAN.md`, `LONG_TERM_PLAN.md`, `MEMORY.md` | Stale root plans. `MEMORY.md` still holds the NAS deployment notes CLAUDE.md points to (`### NAS deployment`). |

Still to do (follow-ups, not in this move): `trading_intel/scheduler/runner.py` (APScheduler; the NAS uses DSM tasks)
and dropping `streamlit` / `fastapi` / `uvicorn` / `apscheduler` from `pyproject.toml` (+ `scripts/nas/image_smoke.py`).

Restore one item, e.g. the Streamlit pages:

    git mv archive/trading_intel/dashboard/pages trading_intel/dashboard/pages
    git mv archive/trading_intel/dashboard/Home.py trading_intel/dashboard/Home.py

## 2026-11-14 (later) - folder cleanup, group B (project doc `claude/folder-cleanup-audit-20261114.md`)

Same rule as above: nothing here is imported, scheduled, tested or run; same relative paths under `archive/`.

| Was | Why it is here |
|---|---|
| root `run_*.bat` (9), `setup_cor_vixex.bat`, `scripts/ship1_deploy.bat`, `scripts/add_jaguar_3q26_watchlist.bat` | July laptop launchers and spent one-shot bats; the NAS DSM tasks replaced them. |
| `COCKPIT_DEPLOY.md`, `DEPLOY_gex_vol.md`, `DEPLOY_vol_surface_cm.md` | Old deploy notes (Jul-Aug); `scripts/nas/deploy.sh` + `DEPLOYMENT.md` cover deploys now. |
| `scripts/probe_tas_legs.py`, `probe_fmp_fields.py`, `probe_transcripts.py`, `probe_swing_endpoints.py`, `check_oi_chain_spx.py`, `verify_oi_flow.py` | One-off data probes / checks (May-Aug). |
| `scripts/normalize_watchlist_symbols.py`, `backtest_vol_regime.py`, `tas_export.py` + `tas_signals.py`, `plot_vol_surface.py`, `dashboard_vol_surface.py` + `watchlist_report.py`, `skew_backfill.py` + root `skew_data.xlsx`, `cleanup_nonsession_rows_20261029.py`, `rebuild_vix_history_20261029.py`, `tilt_score.py`, `cvxmap/close_score.py` | Done backfills, replaced reports, one-off tests, the old ToS-vs-Convex close score. |
| root `Jaguar-3Q-2026-Outlook-DECRYPTED.pdf`, `-Ideas-Table.xlsx`, `-Summary.md` | Copy of the Jaguar 3Q26 outlook (the letters pipeline keeps its own in `research/company/`); nothing reads it. |

Restore one: `git mv archive/<path> <path>`.
