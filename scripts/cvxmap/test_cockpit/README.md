# TEST cockpit (created 2026-11-08)

A copy of the production Convex cockpit (commit `824a06c`, page sha256 `d1486e1d…`) where every change in the MAP master tracker (`claude/map-master-tracker-20261108.md`) sits behind its own toggle. You can test each change live, next to the real MAP.

- **Test page:** http://192.168.1.211:8898/cvx_cockpit_test.html
- **Production page (unchanged):** http://192.168.1.211:8899/cvx_cockpit.html

## How production stays safe
- Production files (`cvx_cockpit.html`, `cvx_live.py`, `am_feed.py`, `market_cal.py`, `serve_cvx.py`, `cvx_collect.py`, `scripts/nas/run_cvxmap.sh`) are locked in `LOCK.json`. `check_lock.py` verifies them, and so does the ship bat before and after its commit. The test server also records the check every day in `data/test/archive/YYYYMMDD/code_*.json`.
- The test side is one container, `cvxmap-test`, on port 8898. It only reads the data files the production writers make, and writes only under `scripts/cvxmap/data/test/`.
- There is no second writer and there are no extra Convex calls. Every toggle runs in the page, from data production already writes: bid/ask, mids and IVs per minute, plus the live Week chain.
- **Two ways back:**
  - the red **ALL OFF = production** button on the test page (all toggles off reproduces production's maths exactly; see the parity test below);
  - the production page itself, which keeps running.
- To remove the test side completely: `sudo bash …/scripts/nas/run_cvxmap_test.sh stop`.

## Toggles
All toggles are off by default and independent of each other. A **v0** toggle uses a first rule whose numbers still come from the stage-1 investigation, and its scores restart when the rule changes.

| Toggle | Tracker | Ver | What it does |
|---|---|---|---|
| One IV clock (off / 16:00 / 16:15) | B2 | v1 | Uses one clock for the 0DTE IV solve and the greeks. The writer solves IV to 16:15, so the page re-expresses it exactly on the chosen clock: σ' = σ·√((16:15−t)/(end−t)), which is exact for r = 0 Black-76. Gamma at the current minute is the same on both clocks; projected columns and charm/vanna differ. |
| Half-day clock | A5 | v1 | On an early-close day (from `ti.json` session), the 0DTE clock, IV and map end at the close. Add `?earlyclose=13:00` to the address to simulate one on any day. |
| Quote filter | B3 | v0 | Drops a side's IV if bid ≤ 0, ask ≤ bid, or spread > 60% of mid. |
| Gap fill | B6 | v0 | A strike without IV keeps its position; its IV is filled linearly from neighbouring strikes. Production drops that strike. |
| Week IV = live | B5 | v1 | Week IV comes from the live chain for the same contract, rescaled to its real time left. Falls back to morning IV + ½×ATM shift where live IV is missing. OI is unchanged. |
| Bad-book check | B4a | v0 | Flag only: ⚠ when the 08:35 book differs from the live chain near spot (median gap > 8 pts, or > 25% of strikes off by > 10 pts), or when IVs fall outside 3–150%. Normal intraday drift is several points; 10/28 was 50+. |
| Smooth β | E2 | v1 | Gaussian blend across zone centres instead of hard steps (offline `smooth.py`, checked by finite differences 11/07). Smile ON only. |
| Full Smile slope | D1 | v1 | Adds Vega·db/dS to Smile gamma. Smile ON only. |
| IV path | C2 | v0 | IV at a map price moves along β from the anchor spot; greeks are computed at that IV. |
| Real 5-min charm | D4 | v1 | Charm = the actual change in delta over the next 5 minutes. |
| Levels from raw | F2 | v0 | Peaks and troughs are found from the raw field's scale, not the colour field's. |
| Display labels | F1, F3–F5 | v1 | The display candidate (`7fd6345a`), reapplied onto `824a06c`: raw vs median-relative labels, flat zones, line rules, Smile OFF/ON check. |
| Support + walls | DN1 (Density) | v0 | Call wall = largest 1–7 DTE call OI above spot (to +8%), put wall = largest 1–7 DTE put OI below (to −8%), round-number support = the 100-pt strike below spot with the most put OI (to −8%). Lines on the cone and odds chart + touch odds. |
| Split axis | DN2 (Density) | v1 | Board levels outside the chart's price range go into a strip under the cone (Don 11/06) instead of ▲/▼ edge labels. |
| Odds extras | DN3 (Density) | v1 | P25 / P75 marks; Don's buckets below breakdown / in pin / pin top → flip / above flip (from the close histogram). |
| Replay summary | DN5 (Density) | v1 | One line: latest cone median / P10 / P90 / P(in pin) / P(above flip) vs session O/H/L, time in pin, support held. |
| Status extras | DN6 (Density) | v0 | Levered-ETF reset at the mark in the status box; 1–7 DTE book date; "thin book" under 150k contracts within ±1.5%; QQQ + Width VIX uses VXN. |
| Regime gauge | VX1 (VIX tab) | v0 | Doublewide VIXM/VIXY 8d/21d EMA gauge with the VIX9D/VIX and SPX-vs-21d-EMA filters, playbook lines, close-shorts / re-entry cues. Untested as a predictor. |
| Variance stack | VX2 (VIX tab) | v1 | VIX² + DSPX² ≈ VIXEQ² session to date, transmission share vs prior close, COR1M / COR3M, identity check. |

The toggles also act on the other tabs that use the Map's maths (Density, Change).

## What gets recorded (`scripts/cvxmap/data/test/`, git-ignored)
- `notes_YYYYMMDD.jsonl`: each "My read" note and each toggle change, with the time, the toggles, spot, Smile and basis.
- `readouts_YYYYMMDD.jsonl`: **scoring v1** read-outs, one per 15-minute slot from 09:45 to 15:45 ET, **taken only while the test page is open**. Each holds production maths plus each maths toggle on its own (B2 16:00, B2 16:15, A5, B3, B6, B5, E2, D1, C2), each with Smile off and on. All use basis OI+vol, ledger off and the All book. Each read-out records:
  - gamma at spot;
  - zero-line bands, peak and trough flat zones;
  - the 15:50 close prediction;
  - the 1DTE and Friday close predictions;
  - the 30-minute implied move;
  - the version ids: page, β file and morning book.
- `scores/`: the nightly scorer (`score_test.py`, 17:20 ET) writes `scores_YYYYMMDD.json`, `s5_forecasts.json` (1DTE and Friday forecasts, pending until their close) and `scoreboard.json`. It recomputes everything from the frozen read-outs and the minute log each night, so the same inputs always give the same scores. The rules are in the docstring of `score_test.py` and in tracker §0.6.
- `archive/YYYYMMDD/`: `live.json` once per 15-minute slot, every intraday version of `ti.json`, `smilebeta.json` and `ledger.json` (gzipped, about 15 MB a day), plus `code_*.json` with the code hashes and the lock check.

## Rebuilding the test page after a production change
`python build/make_test_page.py ../cvx_cockpit.html cvx_cockpit_test.html`

This stops if production is not the locked version. It applies exact single-match edits from `build/tg_module.js` and `build/tg_ui.js`.

## Tests (`tests/`)
- `parity.js`: compares production with the test page (all toggles off) on 11/06 data at 4 clock times. It covers every buildMap grid, colour grid, feature list and map-read text, across basis OI and OI+signed, Smile off/on, books 0DTE/Week/All, and gamma/charm/vanna. 2026-11-08 result: 160/160 identical.
- `test_score.py`: scorer unit tests on constructed price paths (touch / held / broke / unresolved, no look-ahead, early close, lock-in, missing predictions, pending 1DTE then scored, duplicates, determinism). The ship bat runs it first.
- `readout_test.js`: read-out content and determinism across fresh page loads (11/06 data).
- `toggles.js`: per-toggle checks (what moves, the B2 same-gamma check, A5 simulation, B4a on a corrupted book, E2 steps, D1 finite-difference check, C2 path, D4, F2, FX, speed).

## Density + VIX toggles (added 2026-11-08 evening)
- Source notes: `claude/don-density-versions.md` (Don's 11/06 Density version) and `claude/vol-regime-knowledge-20261108.md` (VIXM/VIXY gauge, variance stack).
- Built from `build/tg_den.js` and `build/tg_vix.js` (hooks in section 13 of `make_test_page.py`). Display / odds layers only: none changes the Map, the base cone numbers or the gamma read-outs.
- **Data for VX1 / VX2 / DN6:** `volx.py`, a thread in the test server, reads free Cboe delayed quotes (VIX, VIX9D, VIXEQ, DSPX, COR1M, COR3M, SPX, VXN, VIXM, VIXY) every minute and Cboe daily history (VIX, VIX9D, SPX, VIXM, VIXY) every 15 min — the same CDN `vixdash.py` uses, no Convex. It writes `data/test/volx.json` and `data/test/volx_YYYYMMDD.json` (the day's 1-minute series). The page fetches it only while VX1, VX2 or DN6 is on.
- Already in production, so no toggle: Don's "after 14:00" charm number and the 14:00 marker, and tomorrow's book without the front expiry.
- Not ported: an intraday VIX move split (sticky / parallel / skew / convexity) — it needs a live 30-day SPX chain we don't pull; the daily 08:35 six-factor split on the VIX tab stays. NDX: our QQQ book is not thin, so DN6 only adds VXN for the cone width and a generic thin-book tag.
- Test: `tests/density_vix.js parity` (all off: Density runs, odds, key text, canvas pixels and every VIX section identical to production — OK 2026-11-08 on 11/06 data) and `tests/density_vix.js shots`. Map `parity.js eod` still 40/40 identical.
