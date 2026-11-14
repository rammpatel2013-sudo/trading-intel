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
