# Newsletter Knowledge Digest — 2026-07-27

*Sources read automatically: **Doc McGraw** (SPX Daily Plan), **The Long & Short** (Ed. 23), **Special Situations Research**, **Jaguar Analytics** (ARMK). Each key claim cross-checked against the trading-intel NAS data. Descriptive only — FlashAlpha rule 4.*

## The through-line
All four converge on one setup: a **balanced, range-bound tape** that spent last week refusing to accept prices outside its range, now gapping on the Iran de-escalation into the **biggest catalyst week of the quarter** — Wed FOMC + Powell, Thu advance GDP/PCE, and the Mag7 gauntlet (MSFT/META Wed, AAPL/AMZN Thu). Both the vol/gamma lens (Doc) and the auction/market-profile lens (L&S) say the same thing: **don't trust direction until price *accepts* outside the range.**

## Per-source key reads

### Doc McGraw — SPX Daily Plan (Mon 07-27)
- War premium unwound (Iran strikes paused Fri); SPX gapped +60–70 (~1%) into the **7475–7480 confluence** zone "by gap, not grind."
- Structure: dealers **short gamma above *and* below**; **7500 = net-short, call-driven "chase pocket"** (holding above it is a change-of-character, not a ceiling); dealer-long **wall 7530–7600**; thin air below (7460 → 7445–7450 → 7430 → 7400 → 7375 → 7350).
- Vol: VIX 17.65 (−5%), /VX 18.5 premium; Mon straddle 53.25→30.60; VIX9D ~18 still has decay fuel into Wed = works *for* a bid.
- EM rails: Mon 7445‹›7515 · Wed 7405‹›7555 · Fri 7365‹›7595.

### The Long & Short — Ed. 23 "The Market Never Chose a Side" (Sun 07-26)
- Week defined by **failed discovery**: three attempts to leave balance failed. "Movement is not acceptance; positioning never migrated."
- GOOGL + TSLA earnings gapped futures lower Thu night; buyers defended Fri support — sentiment changed, value didn't.
- Into the catalyst week: **levels > opinions**; watch acceptance above resistance / below support — everything between is rotation.

### Special Situations Research (07-24, macro digest)
- ~30-item macro sweep: Google earnings disappoint; Dan Niles Mag7 preview; **hyperscaler FCF "getting incinerated"**; **TSLA −14%** on AI capex + profit miss; **NVDA in talks for a ~$250B OpenAI backstop**; token costs at 3.5-month lows; new 10–12.5% tariffs; Utz taken private (+88%); Goldman flags upside oil risk; adding to APP (growth) + BWMX (special sit).
- Deep content sits in PDF attachments (State Street / BlackRock / MS Q3 outlooks, Applovin model) — not machine-readable through Gmail here.

### Jaguar Analytics — ARMK "Nexus is New" (07-26)
- Options-flow bull case; original callout Jun 30 off **Dec 60/65 call buying**.
- Catalyst = "Nexus" division serving **remote data-center campuses** (two >$100M hyperscaler contracts already). Morgan Stanley (07-23): ~**700 U.S. target sites**; first two-site contract >$200M/yr at above-average margins, light capex. Kept in the *bull* (not base) case; competitive — Compass/Sodexo hold foodservice at 6/7 Mag7.

## Cross-checks vs our NAS data

| Claim | Source | Our data | Verdict |
|---|---|---|---|
| SPX/SPY dealers **short gamma** | Doc | SPY net GEX **negative every session 07-20→07-26** (−2.3k to −8.8k, "move-amplifying"); gamma-burnoff SPY front-week GEX negative across DTEs | ✅ Confirmed (last week) |
| …but watch the gap | — | **07-27 flipped to net LONG gamma** (+10.3k, spot 745.5 > flip 739) | ⚠️ Refines Doc — gap put us in move-damping now |
| Vol crushed on de-escalation | Doc | VIX 18.7 (07-24) → 17.65 (07-27); term contango, VRP ~8.5, vega zone "low" | ✅ Confirmed |
| **Range-bound / inside balance** | L&S | SPY held **738–748 (~1.3%)**, oscillating around the gamma flip | ✅ Confirmed |
| **TSLA −14%** on earnings | SSR / L&S | TSLA **~378 (07-22) → ~313 (07-24) ≈ −17%**; ATM IV spiked to 0.94 then crushed; flip/regime went null in the dislocation | ✅ Confirmed (our drop a touch larger) |
| **ARMK Dec 60/65 call accumulation** | Jaguar | **1 tape print since 07-16** (a Jul-17 48C, $95k buy); no Dec 60/65 visibility; ARMK not in per-name coverage | ❌ Can't corroborate — coverage gap |

## What we can do
1. **SPX/SPY gamma into FOMC/Mag7.** Our SPY flip sits ~739; today's gap put us just above it (long-gamma / move-damping). A clean re-break **below the flip** re-opens Doc's "air below" — that's the level where our model and his map agree, and the tell that the gap is failing.
2. **Vol is the edge, not price.** VRP ~8.5 + front-vol crush + VIX9D decay into Wed = the "bid dips / sell front vol" tilt both our data and Doc support — *until* Wednesday's FOMC event resets it.
3. **ARMK coverage gap (actionable).** ARMK barely clears our $25k tape floor and isn't in per-name collection. To track Jaguar-style mid-cap flow: add ARMK via `scripts/add_watchlist.py` (captures Dec-60/65 OI in `oi_chain_eod` going forward), or lower the TAS notional floor for flagged tickers.
4. **We're positioned for the earnings week.** The EM-break / gamma-burnoff system (now live) is set to catch post-earnings expected-move breaks on MSFT/META (Wed) and AAPL/AMZN (Thu) — the exact names Special Sits + L&S flag as the resolvers.
5. **Automate this.** Stand up a recurring "read the four newsletters → verify vs our data → append a digest" task so it runs without prompting.

---
*Generated 2026-07-27 from Gmail + trading-intel MCP reads. Sources: docmcgraw.substack.com · longandshortmkts.substack.com · specialsitsresearch.com · jaguaranalytics.com*
