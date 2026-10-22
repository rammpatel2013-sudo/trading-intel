# 2026-10-21 — Front-expiry fix (trading-intel) + TOS 0DTE cockpit (C:\tos-dashboard)

Saved from the 2026-10-21 Cowork chat (deleted afterwards). Same content lives in the claude.ai Project
doc `claude/tos-cockpit.md` and in project memory (`front-expiry-fix`, `tos-live-workbook`).

## Ship (still pending when the chat ended)
Laptop PowerShell:
```
cd C:\Users\drmit\PycharmProjects\trading-intel
scripts\ship.bat "Front expiry everywhere (Convex exps 0-based), 8:35 pull for 0DTE stocks, 10/22 series-change date, Doc note uncut" "letters_fetch oi_chain_eod" trading_intel\timeutils.py trading_intel\clients\convex.py trading_intel\scheduler\jobs\index_walls_am.py trading_intel\scheduler\jobs\oi_chain_eod.py trading_intel\scheduler\jobs\letters_fetch.py trading_intel\mcp\extra_tools.py trading_intel\mcp\profile_tool.py trading_intel\api\wall_conviction.py trading_intel\dashboard\watchlist_metrics.py trading_intel\dashboard\oi_changes.py tests\test_timeutils.py tests\clients\test_convex.py tests\greeks\test_rolling.py tests\scheduler\test_index_walls_am.py tests\scheduler\test_oi_chain_eod.py tests\mcp\test_extra_tools.py tests\mcp\test_profile_as_of.py tests\dashboard\test_watchlist_metrics.py
```
NAS (ship.bat does this step): `cd /var/services/homes/drmithil/trading-intel`.
`oi_chain_eod` (~1 h at 7 calls/min) back-fills the front week into that night's stock books; `letters_fetch` re-saves Doc's note uncut. Both optional.

## Post-ship checks (next morning)
- 8:35 SPX book has dte-0 rows: MCP `get_walls SPX dte_max=0` → found.
- Daily brief section "00b 0DTE gamma map" populates (it was always empty before).
- Cockpit Plan tab: 0DTE walls, AM session straddle, overnight positioning lean.
- `sudo grep stocks ~/ti_index_walls_am.log | tail` lists the 0DTE stocks pulled.

## Mithil's decisions in this chat
- Front expiry everywhere (indexes + stocks; morning, intraday, evening).
- 8:35 pull only for stocks that have a 0DTE that morning (top 30 by OI).
- GEX/DEX/wall history: mark the change date 2026-10-22 (`timeutils.FRONT_EXPIRY_SINCE`), don't keep the old series.
- Tape buy/sell (aggressor) side isn't trustworthy: pure volume only. Overnight OI change × IV vs ATM is the acceptable bullish/bearish guide.
- Mag7 aggregate-flow tab: dropped (clutter).

---

# Reference: TOS 0DTE Flow Cockpit (built 2026-10-21)

Laptop-only system in `C:\tos-dashboard`. Excel + TOS RTD collects; the cockpit visualizes. Since 2026-10-21 evening it also reads **Doc McGraw's morning plan** and **trading-intel's morning book** through `am_feed.py` (laptop → trading-intel code/DB; no NAS change).

## Pipeline
- `TOS_RTD_SPY_SPX_Dashboard_v16.xlsm` + **LiveLog v15** (`LiveLog.bas`, import via Alt+F11). Every 15 s writes `live.json` (via `live.tmp`); once a minute appends cumulative call/put volume, mids and IV for every chain strike to `flow_YYYYMMDD.ndjson` (SPY/SPX/QQQ) and writes the Excel **Cockpit** sheet. `Auto_Open` shows the Cockpit and starts LiveLog 20 s after Enable Content. First live run: 2026-10-21 15:28 ET.
- `am_feed.py` (started by `start_dashboard.bat` with trading-intel's `.venv` python, falls back to `python`): every minute writes `ti.json` = `{doc, ti, errors}`.
  - **doc** = Doc's SPX report parsed with regexes, no LLM: TradingView strings (MAIN levels for SPX/SPY/ES, SCALPER actions, WEEKLY, PREMIUM), rails (STR, 1σ, STR 1.5, OVN EM, Weekly EM, ½σ), pivot/chop zone/working area, up/down paths, THE ONE THING, and the **road map IF/THEN lines** as scenarios. Source order: Gmail (trading-intel's gmail.readonly token, `from:docmcgraw@substack.com newer_than:3d`, includes Trash because read letters get deleted) → `C:\tos-dashboard\doc_today.txt` (manual paste) → NAS ResearchNote `__DOC__`. Polls every 3 min until today's report lands (usually 08:00–08:30 ET), then every 30 min.
  - **ti** = `build_market_read(SPX)` (regime, mechanics, weather, path, triggers), `get_profile(SPX, span 3%, 61 pts, gamma)` all-expiry + next two expiries + flip, `get_walls(SPX, dte_max=7)`. Every 10 min before 10:00, then 30 min.
  - Errors are reported by exception type only (rule 2: nothing from .env is ever printed).
- `tos_cockpit.html` served by `serve.py` (no-cache `http.server`, port 8000). Polls `live.json` every 3 s, the ndjson every 30 s, `ti.json` every 30 s. `?demo=1` = simulated session (own localStorage namespace) with Doc's 10/21 plan embedded.
- Expiry auto-rolls: `Expiries!A2` = next trading day after 16:15 ET, NYSE holidays through 2028; SPX root = SPXW for same-day expiry.
- Deploy gotcha: `device_commit_files` sometimes ships a stale copy on the first call; always md5-check and re-commit.

## Browser cockpit — four tabs (Flow · Strikes · Skew · Plan), shared header + read strip
- Read strip adds a yellow **Doc line** (live lean + latest conversion) and a **Doc pivot** chip.
### Flow
- **Flow by strike** (Ridges / Heat / IV vs ATM / Gamma), spot path, wall/flip/pin lines, burst rings. **Doc levels** button (or D): Doc's levels for the current symbol drawn under the ridges (yellow pivot, teal positive/sticky, coral dashed trigger/gate, grey dotted rails/MAs) with labels on the left; ▲▼ = a conversion, ○ = its target hit (SPX).
- Zoom: box, ＋/−, scroll/Shift+scroll, Shift+drag pan, F/double-click reset. Dealer book ladder. Strike panel (call + put price, their IV, volume with net line). Watch list.
- **Reads**: Doc's road map (lean, latest events, next armed level above/below), hedge flow ±0.25%, charm, vanna, wall conviction, vol-confirms-price quadrant, bursts.
### Strikes — 2×2 grid of the strike chart, per-slot strike menus.
### Skew — smile now vs then, skew through the day, read, term structure.
### Plan (added 2026-10-21 evening)
- **Doc's road map, graded live on SPX**: every IF/THEN line in Doc's words with a state badge — ARMED (pts away) / TOUCHED / LIVE ▲▼ / HIT ✓ (time to target) / REVERSED (closed back before target) / OFF. Conversion rule selector: **5-min close (Doc's rule, default)**, 1-min close, touch. Kinds: cross (converts / is lost), reject (push into X fails), fail/reclaim (only after an earlier conversion at that level), lean (which side of the pivot). Grading uses SPX minute closes from 9:30; 5-min close = the snapshot logged at each 5-minute boundary.
- **SPX against Doc's levels** chart (STR band, levels, path, conversion marks).
- Side: Doc's level table (labels, scalper action, distance), rails; **cross-check** Doc vs TOS 0DTE live vs trading-intel AM (pivot/pin, flip, overhead, underneath, expected range, dealer gamma; ✓ within 5 pts); trading-intel morning book (regime, all-expiry gamma state + flip, weather, 0–7 DTE walls, triggers, gamma-by-spot chart: all expiries vs next expiry vs today's TOS 0DTE).
- Old report (before today's lands): shown with a banner, not drawn, not graded.

## Findings + fixes (2026-10-21 evening) — staged in the laptop repo, ship pending
- **Convex `exps` are 0-based** and index 0 = whatever is listed first at pull time (verified live: SPX `[0]`→10/21 at 17:15; SPY/QQQ at 08:35 still listed 10/20 at `[0]`, which is why their AM book had 0DTE and SPX's didn't; KR/CL/PNC `[0]`→10/23; ZTS `[0]`→11/20). Every pull asked for `range(1, …)`.
  - **Fixed globally in `clients/convex.py`** (`_front_request` / `_live_front`): callers keep passing 1..N; the client asks for 0..N+1, drops settled expiries (any earlier date; today's after 16:15 ET) and keeps the first N live. Covers chain/chain_long/flow_chain/flow_summary/exposures/vix_chain → AM index book, live GEX, intraday flow, EOD stock chains, surfaces, IV term, pre-earnings straddle, VIX options.
  - Readers (`get_walls`, `get_straddle`, `get_oi_changes`, `get_profile`): skip TODAY's dte-0 rows from 16:15; price the dte-0 leg with real time to 16:00 (6.5 h for as-of books). `wall_conviction` stays dte ≥ 1.
- **8:35 pull for stocks with a 0DTE** (`index_walls_am.same_day_names`): names whose newest stored chain lists an expiry dated today (DB read, no vendor call), top 30 by OI. `oi_chain_eod` deletes that day's `convex_am` rows for a symbol before inserting the EOD book (one book per day, no double counting).
- **Series change date** `timeutils.FRONT_EXPIRY_SINCE = 2026-10-22` (Mithil chose "mark the date"): `same_front_basis()` keeps day-over-day reads on one side — weekly ΔGEX / GEX direction (watchlist metrics), wall-conviction stamps, straddle decay (None across it), OI-change frames (use Convex's own `oi_change` across it). SPX EOD net GEX (gex_transition) is unaffected: after the close the front is tomorrow either way.
- `letters_fetch._NOTE_MAX_CHARS` 8,000 → 60,000.
- **Overnight positioning (Plan tab)**: am_feed `oi_lean` for SPX/SPY/QQQ from the two newest 8:35 books (existing data, no new collection): Convex OI change + that strike's IV change minus its expiry's ATM IV change (±0.10 pt dead zone) → opened/closed × bought/sold; customer delta added = |ΔOI|·Δ·sign(ΔIV rel)·100 → bullish / bearish / mixed lean, today's 0DTE and 0–7 DTE, top SPX strikes. Inference, not tape (Mithil: aggressor side not trusted).
- Mag7 tab: proposed, then dropped by Mithil (clutter).
- Tests: related folders pass in the Cowork VM (Py 3.10 needs a `datetime.UTC` shim; project is 3.11+).
- Ship (laptop PowerShell): `cd C:\Users\drmit\PycharmProjects\trading-intel` → `scripts\ship.bat "<msg>" "letters_fetch oi_chain_eod" <18 files>`; NAS dir `/var/services/homes/drmithil/trading-intel` (ship.bat handles it).

## Math (identical in VBA, JS and `ref_engine.py`)
Black-Scholes r=q=0, T = minutes to 16:00 ET / 525,600, OTM-side IV per strike. Positions = start-of-day OI, calls +1 / puts −1. Magnitudes as dealer hedge flow for ±0.25%. Flip = nearest zero crossing within ±1.5%. Walls = largest call OI above / put OI below. Pin = max gamma×OI within 2× straddle.

## Caveats
Volume is unsigned. Minute granularity, no backfill: map and Doc grading start when LiveLog runs. 0DTE IV rises into expiry, so IV reads are measured vs ATM; last 45 min flagged unreliable. Descriptive positioning, not signals (rule 4).

## Excel editing rule
Never openpyxl round-trip the .xlsm. Patch XML surgically. Backups in `_backups\` (incl. `tos_cockpit_pre-doc_2026-10-21.html`, `start_dashboard_pre-doc_2026-10-21.bat`).
