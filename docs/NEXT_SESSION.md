# Next session — GEX-by-strike time series + vol-viz polish (fixed-strike + VIX)

## Goal
Three visualization tracks, in priority order. Track 1 is the main build; 2 and 3
are the carry-over polish items Mithil flagged.

1. **GEX-by-strike time series** for SPX / SPY / QQQ (extensible to the watchlist) —
   the Convex-style "joy-plot / gxoi-by-strike over time" view. Net signed GEX per
   strike, plotted as a strike × time heatmap with spot + flip overlaid.
2. **Improve the fixed-strike vol visualization** on the Ticker page — the current
   `load_fixed_strike_changes` chart is hard to read; redesign it.
3. **VIX dashboard** — bigger lift: the `vix_data` table exists but **no collector
   writes it yet**, so this is "build the collector, then the page."

All three are descriptive regime views — FlashAlpha rule 4, no signals.

## Context (read first)
- `CLAUDE.md` (rule 4 FlashAlpha, rule 7 cost-aware LLM, rule 1 data-source isolation),
  `MEMORY.md` (Formulas, `### NAS deployment`, the 2026-05-23 status + the **sandbox
  gotcha** note — stale/truncated mount views, stale pytest `.pyc`, `index.lock` EPERM).
- Live data in NAS Postgres (`postgresql+psycopg://intel:intel@192.168.1.211:5433/trading_intel`,
  head `0008`): `greeks_chain` (per-strike `ts, expiry, strike, cp, gxoi, dxoi, vxoi,
  oi, iv, gamma…`), `greeks_snapshots` (spot, gex_total, gex_flip, atm_iv), `intraday_flow`
  (SPX/SPY/QQQ 0DTE volume-weighted greeks, 5-min), `quotes_daily`, `vix_data` (empty).
- Dashboard charting = **Plotly** (`st.plotly_chart`; `plotly>=5.22` is a dep). Pages are
  thin shells over pure helpers; session via the `_session_factory()` pattern (see any
  `dashboard/pages/*.py`).

---

## Track 1 — GEX-by-strike time series (primary)

### Data + cadence reality (decide first)
- Net GEX per strike at a given `ts` is already computed: `dashboard/ticker_data.gex_by_strike(chain)`
  returns net signed gxoi by strike (calls +, puts −) — the project's GEX convention
  (MEMORY Formulas).
- A multi-`ts` chain reader already exists: `dashboard/changes.load_recent_chain_snapshots(
  session, symbol, n=...)` → `list[(ts, chain_df)]`, newest first.
- **Cadence:** `chain_snapshot` runs **once daily** (06:45 ET), so today this yields a
  **daily-resolution** time series. Intraday resolution would need either (a) a new/expanded
  intraday `greeks_chain` collector (heavier Convex chain pulls — added cost/rate-limit risk),
  or (b) deriving from `intraday_flow` (but that is volume-weighted gamma over a tight 0DTE
  range, NOT full-chain gxoi). **Recommendation: ship daily-resolution first (free, uses
  existing data); add an intraday chain cadence as a separate follow-up only if wanted.**

### Build
1. **Pure helper** `dashboard/gex_surface.py`:
   - `load_gex_strike_series(session, symbol, *, days=30, expiry_within_days: int | None = None)
     -> pd.DataFrame` — for each stored chain `ts` in range, apply `gex_by_strike` and stack
     into a tidy long frame `[ts, strike, net_gex]`. Reuse `load_recent_chain_snapshots`
     (raise its `n`/add a days-based variant) so there are **no new raw queries** beyond the
     existing chain reader. Optional `expiry_within_days` filter to a near-term gamma view.
   - `gex_strike_matrix(series) -> pd.DataFrame` — pivot to `index=strike, columns=ts,
     values=net_gex` for the heatmap.
   - `spot_flip_overlay(session, symbol, *, days=30) -> pd.DataFrame` — `[ts, spot, gex_flip]`
     from `greeks_snapshots` (reuse `ticker_data.load_snapshot_history`) to draw spot/flip
     lines over the heatmap.
2. **Render** in a thin page `dashboard/pages/6_GEX_Surface.py`:
   - Symbol selector (default SPX/SPY/QQQ, allow any effective-watchlist symbol), a
     date-range / `days` slider, and the optional expiry filter.
   - Plotly heatmap: x = time, y = strike, color = net GEX on a **diverging, zero-centered**
     scale (red short-gamma / blue long-gamma, matching the existing wall/gamma coloring).
     Overlay spot and flip as lines (`go.Scatter` over the heatmap). Add a "latest snapshot"
     bar (net GEX by strike for the most recent `ts`) as a companion chart — that is the
     classic Convex profile.
   - Caption it descriptive-only (rule 4). Empty-state when <1 snapshot.
3. **Tests** `tests/dashboard/test_gex_surface.py`: seed a few `greeks_chain` rows across 2–3
   `ts` (SQLite, per-table `create`, NOT `Base.metadata.create_all` — `chunks.theme_ids` is
   a Postgres ARRAY that SQLite can't compile), assert the long frame and the pivot shapes +
   that net GEX sign matches the call/put convention.

### How to view
`.venv\Scripts\streamlit run trading_intel\dashboard\Home.py` → **GEX Surface** page →
pick SPX/SPY/QQQ. Daily resolution fills in one column per trading day; meaningful after a
few sessions of `chain_snapshot` runs (live data accumulating from the week of 2026-05-26).

---

## Track 2 — Improve fixed-strike vol visualization
- Current: Ticker page renders `dashboard/changes.load_fixed_strike_changes(session, symbol)`
  (sticky-strike ΔIV by strike) + `dashboard/walls.wall_history_frame` (call/put wall drift).
  Mithil finds the fixed-strike chart hard to read.
- **First step: open the Ticker page and screenshot the current chart** to define "better"
  concretely before changing it. Likely improvements: a fixed-strike **IV time-series**
  (one line per tracked strike over the available snapshots) and/or a ΔIV **heatmap**
  (strike × time), normalized around ATM, with a clear legend and diverging color. Keep the
  compute in `changes.py`; only the render changes. Reuse the Track-1 heatmap helper if it
  generalizes.
- Tests: extend `tests/dashboard/test_changes.py` for any new pure helper.

## Track 3 — VIX dashboard (collector first, then page)
This is the data-gap-analysis item #3 — there is **no `vix_data` collector yet**.
1. **Collector** (rule 1: vendor calls only in `clients/`):
   - `clients/fred.py` — VIX, MOVE, HY/IG OAS credit spreads via FRED (`FRED_API_KEY` present).
   - `clients/cboe.py` — VVIX + the VIX term structure (VXST/VIX/VXV/VXMT) via a CBOE scrape
     (noted as not-built in MEMORY). Verify endpoints before wiring.
   - `scheduler/jobs/vix_snapshot.py` — idempotent upsert into `vix_data` (16:45 ET per the
     Schedule); register in `runner.py` + a NAS DSM task.
2. **Page** `dashboard/pages/7_VIX.py` — VIX level with the regime **zones** (<22 carry /
   22–32 fragility / >32 stress; crisis ≈ 38.3 — MEMORY VEGA/VIX zones), VVIX, the term-structure
   curve, and VIX 20-day StdDev (the Thrasher input — thresholds need recalibration, MEMORY).
3. Tests for the collector (mock FRED/CBOE) + the page's pure data prep.
- This feeds the Phase 5 FlashAlpha probability model later; keep it data-only for now.

---

## Constraints / gotchas (carry-over from last session)
- **Cowork mount can serve STALE/TRUNCATED file views** mid-session (canonical Windows files
  are fine via the Read tool). Verify canonical via Read; lint/test against reconstructed
  clean copies if the mount looks wrong.
- **Sandbox Python 3.10**: shim `datetime.UTC = datetime.timezone.utc` before pytest.
  Run `pytest --assert=plain -p no:cacheprovider` to dodge a stale assertion-rewritten `.pyc`
  the mount can't delete (EPERM).
- ruff `select = E,F,I,N,W,B,UP,ANN,S,RUF`, line-length 100; tests ignore ANN/S only (so E501/
  F841/RUF100 still apply to tests — no redundant `# noqa: ANN…` in tests). **Lint only changed
  files**; run ruff from the repo root (isolated dirs misclassify `trading_intel` first-party →
  spurious I001). `S105` on `SCHWAB_TOKEN_PATH` is a known false positive.
- SQLite tests: create the specific tables you need, never `Base.metadata.create_all` (the
  `chunks` ARRAY column won't compile on SQLite).
- **Mithil runs git + all live/NAS infra from PowerShell.** Hand him ONE copy-paste command at
  a time. He commits/pushes; the sandbox can't reach GitHub/NAS/Ollama.
- FlashAlpha rule 4: regime descriptors only, no signals/predictions.

## Verify
- `pytest -q` green + ruff-clean-on-changed-files.
- Locally (DATABASE_URL → NAS): open the GEX Surface page and confirm SPX/SPY/QQQ render with
  spot + flip overlaid; confirm the daily columns fill in as `chain_snapshot` accumulates.
