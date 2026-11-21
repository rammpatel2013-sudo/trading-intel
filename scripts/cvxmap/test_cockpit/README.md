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

## Short strikes (SM, rule sm-v0), added 2026-11-13 (night)
Mithil 11/13 22:59: "use this range as an inner strike to sell credit spread and make money ... its about is this line act as a reaction zone or transition zone". Data behind it: 13 sessions 10/28-11/13, straddle rails alone held about 1 first touch in 4; put-side breaks ran 0.7-1.9 straddles past the line, call-side 0.4-0.7 (`claude/band-compare-20261113.md`).
- **Button** "Short strikes" on the Flow tab (next to Strike lines) and on the Map's gamma pane (next to Profile): one switch, **OFF by default**, display only (buildMap, the read-out and every map number unchanged; off = nothing drawn). Code `build/tg_strike.js`, hooks in section 18 of `make_test_page.py`.
- **Rule sm-v0** (fixed before results; identical in `scripts/cvxmap/rail_score.py` `marks()`): today's 0DTE board, start-of-day OI, naive sign - always, whatever the Map's book / basis / Smile buttons say. Each strike's dealer gamma if price goes there = (call OI - put OI) x phi(d1) / (k sigma sqrt T) x 2.5; straddle = the live 0DTE ATM straddle. Significant = at least 20% of the biggest |gamma| within 3 straddles. Calls: from the **1.5x** straddle rail out to 3x, the first significant **positive** strike (+ the positive ones right after it) = green reaction zone; the short (amber line) = the next strike beyond it. Puts: the same from the **1.75x** rail. None = red rail, "no absorbing strike - transition, stand aside".
- **Live** on the page (current minute). **Scored nightly** by the replay (`scripts/cvx_replay.py` -> `rail_score.py`): the marker frozen at **09:45** plus every morning rail (Doc's, ours: STR, 1σ = 1.25x, 1.5x, 2x) - held / broke / untouched (p1score #17 rules), how far price went past the line after touching it, closed beyond or not; section "Rails: reaction or transition" in the replay report, running table by source x rail x side. Trust a row only after 20+ nights and 20+ touches.
- Tests: `tests/strike.js` (OFF = old test page identical: buildMap + read 28/28, Map canvases, read-out, legend; ON = draws on both canvases, no map number changes, 0 JS errors; page marker = `rail_score.marks()` on the same minute: 16/16 on the 11/13 and 11/06 files), `tests/cvxmap/test_rail_score.py`, `tests/cvxmap/test_am_feed_own.py`.
- Same ship (production, am_feed.py re-locked): our morning rails are now **STR** (= the straddle, about 0.8σ) and **1σ = 1.25 x STR** (it used to show spot ± straddle as "1σ"); the 1σ edges are drawn as rail levels and never used as road-map targets.

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

- `volboard.js` + `parity_vb.js` (2026-11-14): the production VIX tab option **Vol board** (see below). `parity_vb.js` = everything OFF gives the old buildMap / read-out / Map canvases / VIX tab text (production old vs new, test old vs new, production vs test); `volboard.js` = the option itself (off = nothing drawn or fetched; on = signal card, 13 market cards most-unusual first, 40-session fear path, curve, 7 fund cards; phone width; volx.json missing; a FIRE state; an image too old to import the strategy; 2026-11-14 evening: the day tells + VIX futures basis line - off on 11/13 with the basis Normal +0.10, a lit Calm drop + Crowded basis, the live 'so far' wording before 16:15, an image too old to import vol_tells, phone width). Harness: prod_old.html / prod.html / test_old.html / test.html (instrument.py) + the 11/13 files + a volx.json from volx.py, served on :8765, page clock fixed at 11/13 17:30 ET.

## Vol board (production VIX tab option, added 2026-11-14) - inherited by the test page
Not a MAP calculation, so it lives on production (rule 11/09). OFF by default; reads `volx.json` `board` only while on. Rule vf-v2 = `trading_intel/strategies/vol_fade.py` (ARMED after a VIX close at 25+, FIRE on a fear release inside 20 sessions, strong = VVIX 110+, buy SVXY, hold 20 sessions); `volx.py` computes the card's state with that same function, the EOD job `trading_intel/scheduler/jobs/vol_fade.py` (DSM 17:13) banks the official signal and pushes Telegram. The standalone `vol_board.html` page was retired.
2026-11-14 (evening, Jared H harvest): + **Day tells** under the signal card - "Calm drop" (SPX closes -1%+ and VIX ends 1+ pt under what the VIX overshoot model says that drop alone would do = big players already hedged; next 5 days leaned up) and "Rally + fear up" (SPX +1%+ and VIX still up = shaky rally; the normal upward drift went missing), each ON for 5 sessions - and the **VIX futures basis** line under the VIX curve (30-day VX blended from the front two monthly VIX option forwards in `vixmap_YYYYMMDD.json`, minus spot VIX: cheap to enter <= 0 / normal 0-2 / crowded & expensive > 2). Maths in `trading_intel/vol/vol_tells.py`, history numbers frozen from the 2026-11-14 Cboe tests (project doc `claude/jaredhstocks-methodology-20261114.md`). Display only, same option.

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

## VIX map Strike map (VS, rule rv-v0), added 2026-11-19 (night)
- **What:** the VIX map redesign steps C1 + C2 (`claude/vix-map-redesign-spec-20261118.md`). VIX tab → Options → VIX map → button **"Strike map (TEST)"** in the map controls. **OFF by default**, display only; off = production's drawing, scale, hover and read-out exactly (checked by `tests/vixmap.js`).
- **Data:** the VIX map file v3 `books` written by `vix_map.py strike_book()` (cvxmap-wide): the strike book every 30 minutes + the newest minute. Files written before 2026-11-19 have no books → the page shows production's map and a note.
- **C1:** one band per listed VIX strike (0.5 apart below ~20, 1.0 above), drawn where the front forward reaches the strike, edges halfway to the next strike. Value = that strike's own dealer gamma (left) / vanna (right) with the forward on the strike, on the map's basis and dealer side. Top-10 role labels `#rank strike ROLE`: dealers long gamma = FLOOR below VIX / CEILING above / PIN at VIX; short gamma = MAGNET with the arrow toward the strike. Green helps VIX up, red helps it down. Hover card: strength % of the strongest strike, rank of N, customer contracts (OI, assumed side) + today's tape buys / sells, dealer behaviour. 5-minute candles from each minute's VIX, front forward cyan dashed, grey price tag + red dotted line at VIX now.
- **C2:** bands flat between 30-minute books (kink at each refresh, dashed line + red "book" tag at the newest book), white edge lines where neighbouring strikes change sign, white pills around the 4 strongest strikes while they stay ≥ 60% of the scale.
- **Not yet (later steps):** countdown tag, pre-market line, tape box, whales vs crowd in lots, delta volume bars (C3, needs the a1b VIX tape), demand zones + naked POCs (C4, volume-source decision).
- **Test:** `python tests/vixmap_fixture.py <dir>` then serve `<dir>` with `test_old.html` / `test.html` (`instrument.py`) and run `node tests/vixmap.js` (19 checks).

## Flow panel (FP, F1–F3), added 2026-11-19 (night)
- **Button** "Flow panel" in the Flow tab's Flow by strike header (next to Short strikes). **OFF by default**, display only; off = hidden and nothing fetched. Code `build/tg_flowpanel.js`, hooks in section 20 of `make_test_page.py`. Follows SPX / SPY / QQQ.
- **F1 dealer gamma at spot**, every minute of today's 0DTE book, $ per 1% move: OI part (naive sign, start-of-day OI from live.json) + measured part (today's trade tags: customer buy → dealer short), Black-Scholes at each minute's OTM IV, clock to 16:00. Solid = total (= the Map's "OI + signed" at spot), dashed = OI only.
- **F2 OVI** (customer calls bought − sold − (puts bought − sold), contracts, from the open) for 0DTE and the next expiry (minute log `n1`), + 15-minute change. 1–5D / 30D+ split needs the tape writer (later).
- **F3 terrain badge** (rule tr-v0, `scripts/cvxmap/terrain.py` → `data/terrain.json`, written by cvxmap-wide at its 09:26 start; by hand `python terrain.py`): minutes price spent in spot's bin (SPX 5 pts, SPY/QQQ 0.5) over the last 3 sessions → HVN (≥ 70th pct of visited bins) / LVN (≤ 30th) / MID / NEW (never there).
- **Test:** `tests/flowpanel_expect.py <dir> SPX` (independent Python F1 / F2 / F3 for the last minute) then `node tests/flowpanel.js` (14 checks; 11/19 log: page = Python to 1e-6).

## Book clean (BQ v0), added 2026-11-20 — hedge-math review round 2, item 1
- **Toggle** "Book clean" in the yellow bar, **OFF by default**; scored like the others (config `bq` in TG_CFGS). Code `build/tg_book.js`, hook section 21 of `make_test_page.py`. Doc `claude/hedge-math-review-round2-20261119.md`.
- The 1–7 DTE book's IV (08:35 pull, ti.json near_book) goes through a cleaner first. Per strike: **observed** (both sides in 3%–150% and ≤ 10 vol pts apart → OTM side), **conflict** (> 10 pts apart → side closer to the expiry's agreeing neighbours), **paired** (one good side), **interpolated** (neither: from strikes ≤ 3 away on both sides), **unavailable** (left OUT of the map, never guessed); **stale** flag when the live Week chain differs by > 10 pts near spot. The read-out shows the share of each.
- 11/19 SPX book: 1,622 strikes = 1,368 observed · **252 call/put conflicts** · 2 other side · 0 filled · 0 left out.
- **Test:** `tests/book_expect.py <dir> SPX` (independent Python) then `node tests/bookclean.js` (11 checks: OFF identical to the old page; ON counts and every IV = Python, 0DTE map unchanged).

## Week IV (WK v1) + Data check (AB v0) + Close pin (P3 v0) + grouped bar, added 2026-11-20 (night)
- **Why:** check of the Week book on 11/19 vs the vendor's own greeks on 4,000 trades: clock right (time ratio 1.00 on PM weeklies);
  08:35 call IV sat 6.6 (AM monthly) / 2.6 (1-day PM) / ~0.6 (4–6 day) vol pts UNDER put IV near price (live chain: 0.0) → step at
  price + wrong level; vendor prices 1–6 DTE off a forward 5–14 pts above spot, production used spot. Doc `claude/week-book-check-20261120.md`.
- **Week IV** (toggle `wk`, code `build/tg_week.js`, section 22): after 09:35 live Week IV per contract (B5 maths; live Week IVs are on
  whole days - verified vs trade prints); before / where missing the 08:35 book **delta-weighted** (σ = N(d)·put + (1−N(d))·call,
  d = ln(F/K)/(σ_mid√T); > 10 pts apart or one side bad → BQ rule); Week greeks at **F = S + B(expiry)**, B = median forward from
  the live Week deltas (strikes ±40, 0.10 < Δ < 0.90, ≥ 6). Replaces B5 / BQ / V9. 0DTE untouched.
- **Data check** (`ab`): warn, never hide - amber dashed frame + first read-out line with the reasons (feed > 3 min old, Week book
  not today, pre-open, 08:35 call/put gap > 3 pts with WK off, B4a, < 50% near-price Week contracts live after 09:35, > 20% of 0DTE
  strikes within 1% without IV). Far-OTM 0DTE IV rising into the close is NOT a warning (normal tail repricing).
- **Close pin** (`pin`, rule cp-v0): gamma profile projected to 15:50, ±1.5%: long gamma at spot → nearest positive-gamma peak
  (else zone edge); short gamma → "no pin" + flip. Dashed magenta line + read-out; recorded as `close.pin`; scored nightly `S4pin`
  (median error vs the 16:00 close and vs "close = spot now").
- **Bar:** 8 grouped switches (Clock · 0DTE IV clean · Week IV · Smile path (SG + E2) · Real charm · Levels · Data check · Close
  pin); the old single toggles sit under "All single toggles". New scored configs: wk, smp, clock, iv0, wk_smp.
- **Test:** `tests/week_expect.py <dir>` then `node tests/weekiv.js` (25 checks; 11/19 13:17: OFF identical to the old page; basis,
  868 blended and 312 live IVs = Python; Week gamma at spot = independent sum at the forward; 0DTE unchanged; AB / P3 / groups).
  11/19 13:17 effect: Week gamma trough near price moved from ~7,229 to ~7,189.

## 2026-11-21 — promoted to MAIN (Mithil Q1–Q6), test toggles retired
- **Main cockpit now:** Week IV (live Week IV after 09:35, 08:35 delta-weighted before, Book-clean fallback, Week greeks at
  F = S + B), 0DTE IV clean (quote filter + gap fill), ONE 16:00 clock (cvx_live.py solves 0DTE IV to 16:00 and writes
  `iv_end`; the cockpit re-expresses older 16:15 logs / half days via earlySg), buttons **Data check** and **Close pin**
  (off by default). Code: the `mf*` block in cvx_cockpit.html (before greekColNear). 11/19 13:17: Week trough 7,230 → 7,185,
  All-book trough 7,208 → 7,188, 0DTE unchanged.
- **Retired on the test page:** B2, A5, B3, B6, B5, BQ, V9, WK, AB, P3 (files tg_book.js, tg_week.js removed; tests weekiv.js,
  bookclean.js replaced by `tests/mainfix.js` on the production page, 26 checks vs iv0_expect.py / week_expect.py / book_expect.py).
  The test page "off" = the new main. Groups left: Smile path (SG + E2, scored 20 sessions first — Q4), Real charm, Levels.
- **Score:** read-outs before 09:45 are never scored (SCORE_FROM in score_test.py, Q1). Close pin still scored (S4pin) from
  the test page's read-outs (mfPinFrom).

## Orbs+ (OP), added 2026-11-21
Mithil 11/21 "build all together": the replay-video features (strike ridge, corrected dealer-walls / charm / break / push videos) as a live view on the **Orbs** tab. Five TEST buttons on the Orbs header (dashed yellow), all OFF by default; off = Orbs pixel-identical to production and nothing fetched. Code `build/tg_orbsplus.js`, section 22 of `make_test_page.py`.
- **Ridge** (display): each strike as a line, spikes = contracts traded at that strike per block (cyan = calls traded more, pink = puts); the first block and collector gaps are not drawn (unknown minutes). Bubbles hidden while on; candles on top.
- **Flip** (MAP maths): the Map's own OI + vol gamma flip (buildMap + zoneLines with basis oiv and the Orbs Expiries book; the user's basis / Ledger are restored straight after). 11/20 replay: the 10:00 high touched it at 7,192.
- **Walls $** (MAP maths): 5 biggest dealer gamma strikes now on OI + signed ($ per 1%), yellow + / purple −.
- **Notes** (display, rules, no AI): biggest one-block strike volume of the day, first touch of the flip line (from 09:45), price crossing the biggest wall within 30 pts (at most one per 30 min). Newest 4 shown.
- **Strip** (MAP maths + tape): under the Orbs - charm in ES contracts per hour (OI + signed, −Σcharm$ / (50·S); last 20 min cut) and, per 15 min, outside traders' net delta from whales_live.json (orange) vs dealers forced by the move (blue, −gamma$ at spot × move%, model), in ES.
- Greeks come from the Orbs feed (wide_feed: one flat IV per expiry). Not scored yet; the nightly replay will score the flip line (touch → turn) before anything moves to main.
- Tests: `tests/orbsplus.js` 22/22 on the 11/13 files (+ a wide_20261113.json fixture built from the minute log, `orbsplus_expect.py` = independent Python for the strip and the biggest block).
