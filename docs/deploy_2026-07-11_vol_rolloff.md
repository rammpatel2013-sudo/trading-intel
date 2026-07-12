# Deploy runbook — dispersion MCP exposure + RV roll-off (2026-07-11)

Step-by-step to ship the changes from this session: local → GitHub → NAS. These
changes are **local-surface** (MCP server + Streamlit dashboard + a pure lib) —
**no schema migration, no new scheduled job, no new DSM task.** The NAS image
rebuild is only strictly required if you also apply the optional AM-summary
integration in the appendix (it runs as the `am_summary` DSM task).

---

## 0. What changed

| File | Change | Runs where |
|---|---|---|
| `trading_intel/mcp/extra_tools.py` | `get_index_skew` now emits the dispersion family (`cor1m/cor3m/cor_slope/vixeq/dspx/vixeq_vix_spread` + pctiles); **new** `get_rv_rolloff` reader | MCP server (local / Claude Desktop) |
| `trading_intel/mcp/server.py` | registers the `get_rv_rolloff` MCP tool | MCP server (local) |
| `trading_intel/prices/realized_vol.py` | **new** pure transform `rv_rolloff_projection(...)` | lib (used by MCP + dashboard) |
| `trading_intel/dashboard/pages/19_RV_Rolloff.py` | **new** additive Streamlit page | dashboard (local) |
| `tests/mcp/test_extra_tools.py` | tests for dispersion fields + `get_rv_rolloff` | CI |
| `tests/prices/test_realized_vol.py` | tests for `rv_rolloff_projection` | CI |

No `pyproject`/`Dockerfile`/dependency changes. No `alembic` revision. No
`OptionsDataSource` Protocol change. FlashAlpha rule 4 respected — everything
added is a read-only descriptor; nothing writes `signals`.

---

## 1. Local test gate (do this first — rule 6)

From the repo root, venv active:

```powershell
# targeted first
.venv\Scripts\pytest tests\prices\test_realized_vol.py tests\mcp\test_extra_tools.py -q
# then the whole suite + linters (must be green before commit)
.venv\Scripts\pytest -q
.venv\Scripts\ruff check trading_intel tests
.venv\Scripts\black --check trading_intel tests
```

> Note: these were written and the algorithm verified in isolation, but the full
> suite was **not** run in the authoring environment — run it here before pushing.
> If `black` wants to reformat the new page/tool, let it (`black trading_intel tests`).

---

## 2. Commit + push to GitHub

One logical change per commit (repo convention: imperative mood, scope prefix):

```powershell
git checkout -b feature/dispersion-mcp-and-rv-rolloff

git add trading_intel/prices/realized_vol.py tests/prices/test_realized_vol.py
git commit -m "prices: add rv_rolloff_projection (trailing-window RV roll-off)"

git add trading_intel/mcp/extra_tools.py trading_intel/mcp/server.py tests/mcp/test_extra_tools.py
git commit -m "mcp: surface dispersion family in get_index_skew + add get_rv_rolloff"

git add trading_intel/dashboard/pages/19_RV_Rolloff.py
git commit -m "dashboard: add RV Roll-off projection page"

git add docs/learning/vol-newsletter-digest-2026-07-11.md docs/deploy_2026-07-11_vol_rolloff.md
git commit -m "docs: vol newsletter digest + rv-rolloff deploy runbook"

git push -u origin feature/dispersion-mcp-and-rv-rolloff
```

Open the PR (`[mcp] dispersion exposure + RV roll-off`), let CI go green, squash-merge to `main`.

---

## 3. Activate locally (this is where these changes actually take effect)

The MCP server and dashboard run on the **laptop**, so:

```powershell
git checkout main && git pull
```

- **MCP tools (Claude Desktop):** the `get_index_skew` fields + new `get_rv_rolloff`
  only register on a **Claude Desktop restart** (fully quit + reopen). See MEMORY
  `mcp-claude-desktop-setup`. Verify with, in Claude Desktop: *"call get_rv_rolloff
  for SPX"* and *"get_index_skew — show cor1m/vixeq"*.
- **Dashboard:** restart Streamlit to pick up the new page —
  `streamlit run trading_intel/dashboard/Home.py` — then open **"19 RV Roll-off"**
  in the sidebar. (SPX must have recent `quotes_daily` rows; see §5 caveat.)

No migration to run. No DB change.

---

## 4. NAS — code parity (optional now, required only for the appendix)

Your changes add **no scheduled job**, so the NAS collectors are unaffected and
this step is **not required for the features above to work**. Do it to keep the
baked image in sync (and it's mandatory before the appendix's `am_summary`
change would show up on the 7 AM run). Per MEMORY `### NAS deployment` +
`docs/NAS_TASKS.md`: the NAS runs baked Docker images via DSM tasks, git is not
installed there, and `docker` needs `sudo` (DS923+).

```bash
# from the laptop
git push        # main is up to date

# on the NAS (SSH). git isn't installed — refresh the tree via your usual GitHub
# tarball method into the repo dir, PRESERVING .env, then rebuild --no-cache:
ssh drmithil@192.168.1.211
cd /var/services/homes/drmithil/trading-intel
#   ... your established tarball refresh (curl the main tarball, extract,
#       rsync over the tree but keep .env) ...
sudo /usr/local/bin/docker build --no-cache -t trading-intel .
```

- **No new DSM task** — nothing new is scheduled. `scripts/nas/run_job.sh` and the
  existing tasks are unchanged.
- **No migration** against the NAS Postgres (`db-topology`: the one active DB) —
  there is no new revision.
- Sanity after rebuild: `sudo /usr/local/bin/docker run --rm -v \
  /var/services/homes/drmithil/trading-intel/.env:/app/.env trading-intel \
  sh -c "python -c 'import trading_intel.prices.realized_vol as m; print(hasattr(m,\"rv_rolloff_projection\"))'"`
  → should print `True`.

---

## 5. Caveats to verify

- **SPX freshness:** `get_rv_rolloff` / the page default to SPX, but the daily
  `quotes_daily` job only iterates the effective `WATCHLIST`, and SPY/QQQ/SPX are
  intentionally dropped from the default watchlist. SPX rows exist via
  `scripts/backfill_quotes.py --symbol SPX`. Confirm SPX is either in the deployed
  NAS `.env` `WATCHLIST` or refreshed by a task, else the projection reads stale
  closes. (If SPX isn't kept fresh, point the tool/page at a liquid name that is.)
- **Newsletter ingest** (separate from code deploy): drop the 4 `.docx` into
  `research/doc/`, then `python -m trading_intel.memory.sync_knowledge --skip-research`
  (embeddings run on local Ollama). Confirm `research/` is gitignored — the posts
  are subscription content.

---

## 6. Batch 2 — EOD Flow Report (analytics core + MCP tool)

Added in the same session; same deploy shape (laptop-local, **no migration, no new
DSM task**). `get_flow_report` reads the durable `tas_daily_flow` +
`tas_daily_contract` tables, which the **existing** `tas_daily_rollup` EOD job
already populates — so nothing new needs scheduling.

| File | Change |
|---|---|
| `trading_intel/flow/report.py` | **new** longitudinal analytics: `accumulation_trend`, `contract_lifecycle`, `new_vs_fading`, `build_flow_report` (pure + loaders) |
| `trading_intel/mcp/extra_tools.py` | **new** `get_flow_report` reader (+ import) |
| `trading_intel/mcp/server.py` | registers the `get_flow_report` MCP tool |
| `tests/flow/test_report.py` | **new** tests (4) — verified passing in this session |

```powershell
.venv\Scripts\pytest tests\flow\test_report.py -q       # 4 passed
git add trading_intel/flow/report.py tests/flow/test_report.py
git commit -m "flow: add longitudinal EOD flow report (trend/lifecycle/churn)"
git add trading_intel/mcp/extra_tools.py trading_intel/mcp/server.py
git commit -m "mcp: add get_flow_report tool"
```

Activation: **restart Claude Desktop** (registers `get_flow_report`). Verify:
*"call get_flow_report, lookback 21d"* — needs `tas_daily_flow` populated (it is,
if the `tas_daily_rollup` DSM task has been running). Presentation layer (EOD HTML
/ Excel / Streamlit page) is a follow-up on top of `build_flow_report`.

---

## Appendix — optional AM-summary bullet (apply + test + NAS rebuild)

Not shipped in this batch because `synthesis/am_summary.py` feeds the load-bearing
6:55 AM `am_summary` DSM job and the test suite couldn't be run in the authoring
env. To add an "RV roll-off" line to the morning note, mirror the existing skew
wiring in `synthesis/am_summary.py` (per the code map):

1. add an `rv_rolloff` field to the `AmContext` dataclass;
2. add a `_load_rv_rolloff(session, *, as_of)` loader next to `_load_index_skew`
   that reads SPX `quotes_daily` closes and calls
   `prices.realized_vol.rv_rolloff_projection`;
3. assemble it in `build_am_context(...)` where `index_skew`/`skew_extremes` are built;
4. add a `_rv_rolloff_section(ctx)` renderer (copy `_skew_section`) and append it in
   `build_tables_markdown(ctx)`.

Then: `pytest tests/synthesis -q` must pass → commit → **NAS image rebuild (§4)** so
the 6:55 AM task picks it up. Ping me and I'll write this against the real file
once you can run the synthesis tests.
```

*Author's note: the Cowork sandbox mount served stale/truncated copies of some
repo files this session, so tests were validated by isolated logic checks + source
`py_compile`, not a full `pytest` run. Treat §1 as mandatory.*
