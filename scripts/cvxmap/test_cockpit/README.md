# TEST cockpit (created 2026-11-08)

A copy of the production Convex cockpit where every change in the MAP master tracker (`claude/map-master-tracker-20261108.md`) sits behind its own toggle. You can test each change live, next to the real MAP. First built from commit `824a06c` (page sha256 `d1486e1d…`); rebuilt 2026-11-09 from the production page with the Orbs tab, the Density / VIX options and the VIX map (page sha256 in `LOCK.json`).

**Rule (Mithil, 2026-11-09):** only NEW MAP calculations and their testing live here. Everything else goes on the production cockpit (8899) as an option or tab, OFF by default. The Density toggles DN1–DN6 and the VIX toggles VX1 / VX2 moved to production on 2026-11-09 (Density tab "Options" row, VIX tab "Options" row); the test page inherits them from production like any other production feature.

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
| Shadow | SG | v0 | (2026-11-13) Alma's shadow greeks: IV moves with price by the measured β; gamma + vanna·β/S; the Delta pane uses the delta at that IV. Overrides Smile while on. See "Delta map + Shadow" below. |
| Week IV = VIX9D | V9 (#21) | v0 | (2026-11-13 overnight) Week book IV re-marked by the change in VIX9D since the first reading of the day (`live.json` `v9dq`) instead of ½ × the change in 0DTE ATM IV. Production never uses VIX9D (`cvx_live.py` writes `v9d = 0`, `V9D_INTO_MAP = False`). Recorded as config `v9` every 15 min; every read-out also stores `wkiv` = each rule's median error vs the live week chain, judged nightly by `v9_score.py` (called by `score_test.py`, verdict after 10 days). |

The toggles also act on the other tabs that use the Map's maths (Density, Change).

## Delta map (DL) + Shadow (SG), added 2026-11-13 (rules dl-v1)
Mithil's yes 2026-11-12 to QuantNug's delta heatmap ("where supportive / bearish zones to buy / sell are"), and 2026-11-13 00:10 "combining both knowledge to make our map with shadow delta/gamma" (Alma: GEX is "the delta of delta", "only following GEX blindly without DEX won't make you catch the dealer flow"; her shadow gamma = gamma + vanna × the measured spot-vol beta).
- **SG Shadow** (toggle, v0): each option's IV moves with price along our measured β (the Smile button's `smilebeta.json`) from that minute's price, integrated exactly zone by zone (Simpson with E2). Gamma = gamma + vanna × β/S (Alma's shadow gamma); the Delta pane = plain option delta at that IV; charm / vanna use the path IV. Overrides Smile while on (Smile = slope of the min-variance hedge Δ + Vega·β, a different object). Checked on 11/12 12:06: shadow gamma = finite difference of shadow delta within 0.07% (plain: exact); shadow gamma at spot 769 vs plain 995 (All book) — the measured β takes about a quarter off the long gamma there. C2 (IV path, 8-step) is untouched.
- **Delta** (right pane button) computes dealer delta **directly** (kind "delta": Σ position × N(d1), the part that moves) for the same book, basis, clock and toggles. Two views (switch next to the button):
  - **From here**: ES contracts dealers would buy (green) / sell (red) on the way from that minute's price to each level, H = −F × (D(level) − D(price)); F = 2 ES per SPX option delta (SPY via the live ratio, QQQ in $M). Lines every N ES, dotted = net hedge back to zero, #1–#6 = peaks of |gamma| now (shadow gamma with SG): solid = long gamma (absorbs), dashed = short (speeds).
  - **By strike**: each strike's own buy / sell as SPX travels from that minute's price to the strike, as a band at the strike (QuantNug's look). Green below price = support, red below = speeds a drop, red above = resistance, green above = speeds a rise. The two views don't add up to each other (one sums all strikes at a level, the other shows one strike at its own level).
- Sizes are **model** ES: the naive book counts every contract of the basis as dealer-held (on OI + vol all of today's volume), so read them as relative.
- Hover / tap for the numbers. The read-out keeps Charm (same text as with Charm selected); buildMap for gamma / charm / vanna and every other number are unchanged with SG off (parity: production vs test, old vs new test page).
- Scoring: each 15-minute read-out records `dl` (SPX, OI + vol, All book, production maths, Smile off) with `levels` (IV held) and `levels_sg` (Shadow), and the `sg|P`, `sg|S` configs join the scoring v1 read-outs. `delta_score.py` (run at the end of `score_test.py`, DSM 17:20) scores each mode: first touch held / broke (S2 rules) vs a placebo level 10–15 pts away on the same side; verdict after 20+ days and 40+ resolved touches per class. Tests: `tests/test_delta_score.py`.

## Strike lines (SL) + Zero line (ZR), added 2026-11-13 (afternoon)
Mithil 2026-11-13 14:12, after a vendor chart with labelled strike lines ("learn visualization how strike line look ... just like orbs"; "gamma flip line, we need similar line zero vanna on vanna, charm MAP"). Display only: no map number changes (parity on the 11/13 files: test page old vs new 63/63 with both off, production vs test 54/54). Code: `build/tg_lines.js`, hooks in section 15 of `make_test_page.py`.
- **Strike lines** (Flow tab, button next to AM levels, OFF by default): the current minute's value at each strike as a full-width line across the Flow map with a label at the right edge, the 10 biggest strikes in view (under 8% of the biggest dropped), thickness + glow by size, ★ = biggest. Source switch:
  - **Delta** = the Delta map's *By strike* value now: ES contracts that strike's options alone make dealers buy (green) / sell (red) as SPX travels from the current price to the strike (same IV rule as the Delta pane: held, or shadow with SG). Model size, naive book: read as relative.
  - **Gamma** = dealer gamma at the strike now, $ per 1% move, as on Orbs: yellow + (absorbs) / purple − (speeds).
  - Book = the Map's gamma book, positions = the Map basis. The lines show NOW across the whole window; the history is on Orbs and the Delta pane. Not scored separately (the Delta levels are already scored by `delta_score.py`).
- **Zero line** (Map tab, right pane, button shown for Charm / Vanna, OFF by default): the same rule and look as the Gamma *Flip line* (`zoneLines`: in each minute's column the zero crossing nearest that minute's price, joined while it moves ≤ 6 rows), label "vanna zero 7,501 · zone 7,497–7,505" or "charm zero …"; "no vanna zero in view: dealers sell everywhere …" when the column never changes sign. It follows the **raw** value: with the Week / All book the vanna / charm colours are relative to the column median, so the colour edge is not the zero (production behaviour, unchanged).
- Density **09:30 cone** (DN7) went to the production page as a Density option (not a MAP calculation, rule 11/09), so the test page inherits it.

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
- `main_options.js` (2026-11-09): checks on the PRODUCTION page (`prod.html`) for the Orbs tab, the Density / VIX options and the VIX map — every tab renders, every option toggles on / off, nothing is fetched while off, Orbs hover / slider / blocks / Net diverging bars, the live file appends the newest minute, VIX map sign switch, 0 JS errors also with the new data files missing. Needs `wide_*`, `wide_live_*`, `wide_trunc_YYYYMMDD.json` + `wide_live_trunc_SPX.json` (day cut at 15:50 and its 15:51 minute), `vixmap_*` and `volx*` in `srv/`.

## Density + VIX toggles (added 2026-11-08 evening; MOVED to production 2026-11-09)
Moved to the production page as options (OFF by default) on 2026-11-09 — see below for history. `build/tg_den.js`, `build/tg_vix.js` and `tests/density_vix.js` were removed; their code now lives in `cvx_cockpit.html` (`dno*` / `vxo*` functions), and the Cboe poller `volx.py` moved to `scripts/cvxmap/volx.py` in its own container `cvxmap-volx` (`scripts/nas/run_volx.sh`), writing `data/volx.json` (no longer `data/test/volx.json`). The test server no longer starts it.

History (2026-11-08):
- Source notes: `claude/don-density-versions.md` (Don's 11/06 Density version) and `claude/vol-regime-knowledge-20261108.md` (VIXM/VIXY gauge, variance stack).
- Built from `build/tg_den.js` and `build/tg_vix.js` (hooks in section 13 of `make_test_page.py`). Display / odds layers only: none changes the Map, the base cone numbers or the gamma read-outs.
- **Data for VX1 / VX2 / DN6:** `volx.py`, a thread in the test server, reads free Cboe delayed quotes (VIX, VIX9D, VIXEQ, DSPX, COR1M, COR3M, SPX, VXN, VIXM, VIXY) every minute and Cboe daily history (VIX, VIX9D, SPX, VIXM, VIXY) every 15 min — the same CDN `vixdash.py` uses, no Convex. It writes `data/test/volx.json` and `data/test/volx_YYYYMMDD.json` (the day's 1-minute series). The page fetches it only while VX1, VX2 or DN6 is on.
- Already in production, so no toggle: Don's "after 14:00" charm number and the 14:00 marker, and tomorrow's book without the front expiry.
- Not ported: an intraday VIX move split (sticky / parallel / skew / convexity) — it needs a live 30-day SPX chain we don't pull; the daily 08:35 six-factor split on the VIX tab stays. NDX: our QQQ book is not thin, so DN6 only adds VXN for the cone width and a generic thin-book tag.
- Test (2026-11-08, removed 2026-11-09 with the toggles): `tests/density_vix.js` parity and shots. Map `parity.js eod` still 40/40 identical.

## Color strip (CO, #87) + V9 (#21), added 2026-11-13 (overnight run)
Mithil 11/13 20:18 "build" (#87, reversing the drop) and the backlog cut (#21 = KEEP, GO; test cockpit, scored vs the current rule).
- **Color** (Map > Gamma button, dashed TEST outline, OFF by default; code `build/tg_color.js`, section 17 of `make_test_page.py`): a strip under the gamma map with the gamma that **time alone** adds or takes away at each price: bars = gamma at now + 30 min minus gamma now, dotted = now -> 15:45, both read straight from the map's own projection (same book, basis, clock and toggles; no new trades). Green = that level strengthens (dealers lean harder against moves there), pink = it lets go. Display only.
- **V9 Week IV = VIX9D** (toggle): see the table above. `tests/v9color.js` checks: shift = Δ v9dq, only the Week / All books change (0DTE identical), both rules' errors recorded, config `v9` in the 15-min record.
- Parity on the 11/13 files: production vs test 40/40, old test vs new test 40/40 with all toggles off.
