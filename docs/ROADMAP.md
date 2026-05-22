# trading-intel — Build Roadmap

_Dated 2026-05-22. Companion to MASTER_PLAN.md — maps the 7-phase plan against what is actually built and running now, with dependencies, data-accrual gates, and rough effort (S/M/L)._

## Where we are now

- **Phase 0 (foundation)** and **Phase 1 (Convex client + Greeks ingestion): DONE.**
- **Daily collection is LIVE** on the Synology NAS — `chain_snapshot` (per-strike chain), `greeks_snapshot` (aggregate GEX/DEX/VEX/CHEX), `gex_rolling` (6-month rolling GEX), running 17:00 NAS = 6 PM ET against a Postgres container. History accruing from **2026-05-22**.
- Built this session beyond Phase 1: options flow + multi-leg package detection, IV surface (delta + moneyness + term/forward vol), surface read-through report, **fixed-strike & ATM day-over-day change panels**, **call/put wall tracker**.
- ~101 tests passing.

## Not yet built

- **Phase 2** Streamlit dashboard pages — the hosted app is still a scaffold; rich output lives only in the vol-surface HTML script + CLI.
- **Phase 3** macro theme KB — `pdf_pipeline.py` / `tagging.py` scaffolded, not wired/active.
- **Phases 4–6** — strategy ports/alerts, AM summary + anomaly detection, earnings ripple, GEX-VEX heatmap: not started.
- **Phase 7** hosting — collection is on the NAS; the dashboard itself is not hosted yet.

## Roadmap (ordered, by track)

### Track A — Make it visible (highest near-term value; data ready)
- **A1. Per-ticker dashboard page** — price + Bollinger + GEX/DEX + RSI + walls + change panels in one Streamlit page. Dep: data (have). Gate: none. Effort: **M**. (Phase 2)
- **A2. GEX-VEX heat map** — 2D dealer-flow surface (spot% × IV-shock), flip boundary. Dep: chain+surface (have). Gate: none. Effort: **M**. (Phase 6 item, data-ready now)
- **A3. Quick reads** — wall-vs-spot distance, GEX flip vs spot, biggest fixed-strike movers. Dep: data (have). Gate: none. Effort: **S** each.

### Track B — Vol-spike early warning
- **B1. VIX / VVIX + term-structure dashboard (#23)** — Convex OI + CBOE futures term structure. Gate: none. Effort: **M**.
- **B2. Thrasher dispersion recalibration** — re-fit VIX/VVIX SD thresholds on 2020–2025. Dep: historical VIX/VVIX. Gate: none (historical, not accrual). Effort: **M**.
- **B3. 5-condition confluence vol-spike model** — Thrasher + VIX zone + credit spreads (FRED) + GEX:RVOL decline + spot-up/vol-up. Dep: B1+B2. Gate: needs ~2–3 wks of GEX:RVOL history. Effort: **L**.

### Track C — Intelligence layer (mostly data-gated)
- **C1. AM-summary generator (7 AM, Claude)** — synthesize the morning read. Dep: feeds (have most). Gate: improves as data accrues; buildable now. Effort: **M**. (Phase 5)
- **C2. Anomaly detection** — spot-up/vol-up, GEX flip cross, DEX strike migration, fixed-strike repricing, QOPEX. Dep: C1 + change panels. Gate: needs daily history. Effort: **M–L**.
- **C3. Macro theme KB** — PDF ingest → embed → tag → pgvector search. Dep: Ollama/Voyage embeddings. Gate: none (independent of market data). Effort: **L**. (Phase 3)
- **C4. Earnings ripple engine** — direct demand vs share-shift vs sentiment contagion. Dep: earnings data + C3. Gate: needs an earnings cycle. Effort: **L**. (Phase 6)
- **C5. Probability model** — combines Greeks + VIX + ATM IV + credit spreads (the only thing allowed to upgrade descriptors → signals). Dep: most of the above. **HARD GATE: 4–8 weeks of accrued/tagged data.** Effort: **L**. _Do not start before ~July._

### Track D — Hosting / polish
- **D1. Host the Streamlit dashboard on the NAS** behind a reverse proxy, so you view it in a browser (not HTML files). Dep: A1. Effort: **M**.
- **D2. Optional 2nd morning collector run** for settled OI. Effort: **S**.

## Recommended order

1. **A1 per-ticker dashboard + A3 quick reads** — immediate payoff, uses data you already store.
2. **D1 host it on the NAS** — actually see it in a browser, every day.
3. **B1 VIX dashboard → B2 Thrasher recalibration.**
4. **C3 macro theme KB** in parallel (it's independent of market-data accrual).
5. Let data accrue ~4–8 weeks, then **C1/C2 AM summary + anomalies**, then **C5 probability model**.

## Data-accrual gates (time, not effort, is the blocker)

| What | Needs |
|---|---|
| Change panels / wall drift | 2 days (≈ tomorrow) |
| Sticky-strike regime, GEX:RVOL trends | ~2–3 weeks |
| Probability model (C5) | 4–8 weeks |

## Guardrail (unchanged)

FlashAlpha rule: GEX/DEX/VEX/CHEX, walls, and surface reads are **regime descriptors, not signals**. No alerts off raw Greek crossings until the probability model (C5) exists.
