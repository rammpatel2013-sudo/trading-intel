# Replay harness (cloud, 2026-10-28) — superseded on the NAS by `scripts/cvx_replay.py`

**Nightly use:** `scripts/cvx_replay.py` does all of this on the NAS, from the Convex files only, as
DSM task "cvx replay" (Mon–Fri 22:15):
`bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_script.sh cvx_replay`
Outputs: `reports/replay/YYYYMMDD/` (scorecard HTML, video, density / charm / levels / seven-test JSON),
`reports/replay/history.csv`, and a Telegram push. Re-run a day with `--date YYYYMMDD`.

The node scripts below are the first version, run in the cloud on 10/28 against the ToS minute log.
They are kept for reference only.

Runs Mithil's own v2 cockpit page headless over a finished session, so the Density cone, the
charm clock and the map's levels are the page's own calculations, not a re-implementation.

Inputs for day D:
- `C:\tos-dashboard\flow_D.ndjson` (ToS minute log), trimmed to t <= 16:00
- Convex raw `raw_D.ndjson.gz` → `oi_D.json` (0DTE OI from the first idx pull >= 09:31) and
  `quotes_D.json` (per-minute SPX 0DTE bid/ask)
- `tos_cockpit_v2.html` copied to `site/`; `tos_cockpit_v2_replay.html` = the same page with
  buildMap/mapRowsAt/greekCol/liveClock added to `window.__cockpit` (debug handle only; no maths changed)
- live.json rebuilt for D (ts sets the page clock; rows carry the Convex OI), see build_live.py

Steps: `python3 -m http.server 8765` in site/ → `node run_density.js <dir>` (78 density runs) →
`node run_frames.js <dir>` (replay video frames) → `node run_levels.js <dir> oi|oiv|vol` (map
levels per 2-min column) → `node run_charm.js <dir>` (charm clock) → `python3 seven_tests.py <dir> <basis>`.
Playwright + Chromium required. The trading-intel image has Python Playwright + Chromium, but not node.
