# Next session — build the per-ticker dashboard page (Roadmap A1 / Phase 2)

## Goal
Create a per-ticker Streamlit dashboard page that surfaces everything now being
collected, in one view. This is the highest-value next step: the hosted app is
still a scaffold and rich output currently lives only in a standalone HTML script.

## Context (read first)
- Repo: `C:\Users\drmit\PycharmProjects\trading-intel`. Read `CLAUDE.md`, `docs/ROADMAP.md`.
- Daily data is LIVE in Postgres on the Synology NAS (see memory: **nas-deployment**).
  Connect from the laptop: `DATABASE_URL=postgresql+psycopg://intel:intel@<NAS_IP>:5433/trading_intel`.
  Tables: `greeks_chain` (per-strike: strike, cp, expiry, gxoi/dxoi/vxoi, delta, iv, ts, symbol),
  `greeks_snapshots` (aggregate gex/dex/vex/chex_total, spot, gex_flip, atm_iv),
  `gex_rolling` + `gex_term`. History started 2026-05-22.
- Reuse existing analytics — do NOT duplicate query/compute logic:
  - `greeks/surface.py` (build_delta_surface, build_surface_grid, forward_vol)
  - `greeks/surface_changes.py` (fixed_strike_changes, atm_term_changes)
  - `greeks/walls.py` (compute_walls)
  - `dashboard/changes.py` (load_recent_chain_snapshots, build_change_report)
  - `dashboard/walls.py` (load_wall_history, build_wall_report)
  - Reference renderer: `scripts/dashboard_vol_surface.py`.

## Task
Create `trading_intel/dashboard/pages/1_Ticker.py` per CLAUDE.md "Adding a new dashboard page".
- Symbol selector from `settings.watchlist_symbols`.
- Panels (ABBV-style, per MASTER_PLAN / MEMORY):
  1. Price + SMA + Bollinger Bands + GEX overlay (price via yfinance or stored quotes; GEX from greeks_snapshots/greeks_chain).
  2. GEX bar chart by strike + rolling avg + normal-dist fit; mark `gex_flip` and `spot`.
  3. DEX bar chart by strike + rolling avg.
  4. RSI.
  - Plus sections: call/put walls (`build_wall_report`) and day-over-day change panels (`build_change_report`).
- Read the DB via `make_session_factory(get_settings())`; inject the session from `dashboard/Home.py` (composition root) via `st.session_state`.

## Constraints / gotchas
- **File-edit tools truncate large files on this repo** — write/modify via shell heredoc and `python -c "import ast; ast.parse(...)"` after each change (memory: cowork-file-truncation).
- ruff: `select = E,F,I,N,W,B,UP,ANN,S,RUF`, line-length 100, tests ignore ANN/S. Run `ruff check --fix`.
- Fresh sandbox: `pip install --break-system-packages ruff pytest streamlit ollama pydantic-settings structlog scipy scikit-learn sqlalchemy pgvector` (pandas/numpy present). Sandbox Python is 3.10 but repo targets 3.11 — for `from datetime import UTC` code, shim `datetime.UTC = datetime.timezone.utc` before running pytest.
- Streamlit pages aren't unit-testable headlessly — factor data-prep into PURE functions and keep the page thin. Test new pure helpers; use in-memory SQLite for DB-read helpers (`Model.__table__.create(engine)` for only the needed table).
- pytest green + ruff clean before done. Hand Mithil ONE copy-paste command at a time (prefix with `cd C:\Users\drmit\PycharmProjects\trading-intel;`). He runs git + live infra.
- FlashAlpha rule: descriptive regime read-throughs only — no signals/alerts.

## Verify
Run locally: `cd <repo>; .venv\Scripts\streamlit run trading_intel\dashboard\Home.py` (DATABASE_URL pointed at the NAS), open the Ticker page, confirm panels render. Change/wall drift needs >=2 days (live from 2026-05-23).
