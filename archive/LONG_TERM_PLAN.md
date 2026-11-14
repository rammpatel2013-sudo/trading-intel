# LONG_TERM_PLAN.md — The strategic roadmap

> Where MASTER_PLAN.md answers "what do I build in 12 weeks?", this document answers "where is this going over 1–3 years, and what does success look like along the way?"

---

## The mission (one paragraph)

Build a personal institutional-grade research system that compresses morning prep from hours of scattered work into a 5-minute read, while accumulating tagged historical observations that eventually power a probability model for high-confidence vol spike calls. The system is not a trade-execution tool — it is an **edge-discovery and decision-support tool**.

---

## Time horizons

### Year 1 — "Build the engine" (Months 1–12)

**Months 1–3 (Phases 0–3):** Foundation laid. Data flows. Macro KB indexed.
- Convex data ingesting into local Postgres
- Per-ticker GEX/DEX/VEX/CHEX surfaces visible
- 9 jdscan PDFs + ongoing daily PDFs ingested into pgvector
- Basic Streamlit dashboard usable

**Success at Month 3:** You open the dashboard each morning and find at least one piece of information you wouldn't have noticed otherwise.

**Months 3–6 (Phases 4–5):** Strategy ports + automated AM summary live.
- JD Intown, options flow, internals composite, fib all producing signals
- Discord alerts firing reliably
- 7 AM AM summary generated daily via local Ollama
- Anomaly detection wired (7 checks)

**Success at Month 6:** AM summary is actionable on 3-of-5 days. You stop opening Twitter/research sites for morning prep.

**Months 6–9 (Phases 6–7):** Production-grade. Earnings ripple + heatmap. DO deployment.
- Earnings ripple engine working on real ER calendar
- GEX-VEX heatmap matches nextSignals quality
- System runs unattended on DO droplet (or hybrid: data on DO, Ollama on laptop)
- Phone-accessible via secure URL

**Success at Month 9:** You can be away from your computer all morning and still get the AM summary on your phone. System runs 7 days unattended without intervention.

**Months 9–12:** Probability model bootstrap + knowledge gap closure.
- 4–8 weeks of tagged observations accumulated; probability layer activated
- Backtest the 5-condition confluence vol spike model on 2020–2025
- Fill the 5 knowledge gaps (FWDVOL, fixed-strike vol, autocallables, dispersion, OXO2Q1) one per ~6 weeks via `docs/learning/`

**Success at Year 1:** The system has tagged 60+ macro themes, classified 100+ earnings events for read-through, and produced 250+ AM summaries. You have your own data corpus that no public tool replicates.

### Year 2 — "Refine the edge" (Months 13–24)

This is where pure tooling stops mattering and **analytical refinement** takes over.

- **Probability model in production.** 12 months of historical confluence signals → backtest → forward-test → put live. Track precision/recall monthly.
- **Earnings ripple model calibration.** With 200+ ER events tagged, you can move from rule-based classification to a learned classifier (XGBoost or simple logistic regression on flow/IV features).
- **Volatility playbook hardens.** Thrasher recalibration done. VEGA zone transitions backtested. Persistence-decay rules quantified rather than asserted.
- **Decision journal.** Add a fifth memory layer: every signal you act on (or consciously ignore) gets a journal entry. After 6 months you have data on YOUR decision quality, not just the system's.
- **Possible move to better LLM.** If Ollama quality limits the AM summary, switch to Claude API or Groq. By Year 2 you'll have the budget signal (is this worth $5–20/mo?) and the comparison data (Ollama vs API quality on identical prompts).

**Success at Year 2:** You're acting on system output (paper or real, your call) and you can quantify hit rate / edge against a benchmark.

### Year 3+ — "Decide what this is for"

By Year 3, you'll know whether this is:
- **A personal moat.** Keep using it for your own trading. Iterate on weak spots. Possibly add execution (via Schwab API — that's why we kept the credentials parked).
- **A research product.** Open the dashboard (or a subset) to other traders. Charge for it. Becomes a business. Requires auth, multi-tenancy, support — different problem.
- **A consulting wedge.** Use the analytical capability as evidence in conversations with funds, prop firms, or independent traders. The system is the demo.
- **A side experiment that informs your day job.** Maybe the daily reading + Year 1's knowledge gap closure transforms your medical career framing of risk/reward, decision-making, and Bayesian thinking, more than it makes money. Also valid.

The Year 3 decision is downstream of what Year 1 and 2 actually produce. Don't pre-commit.

---

## Budget over time

| Year | Monthly cost | Notes |
|---|---:|---|
| Year 1, Months 1–6 | ~$0 + ConvexValue | Local dev only; Ollama free |
| Year 1, Month 7 (DO deploy) | +$12 droplet (skip managed PG, self-host on droplet) | Stretch budget |
| Year 1, Months 7–12 | $12 + ConvexValue | Steady state, no LLM API |
| Year 2 (if API upgrade) | $25–40 + ConvexValue | Adds LLM API + maybe a TLS-fronted Cloudflare Tunnel + domain |
| Year 3+ | Depends on direction | Product → multi-tenant infra. Personal → same. |

**The single largest controllable cost is the LLM.** Stay on Ollama for free as long as quality is acceptable. Groq's free tier is the fallback for when you go cloud-only. Claude API is the upgrade path if/when you can justify it.

---

## Skill development (what YOU need to learn)

The system is only useful to the extent you can interpret what it shows you. Plan for these:

| Skill | Time investment | Why |
|---|---|---|
| **Fixed strike vs floating vol intuition** | ~2 hours reading + 4 weeks watching it on the dashboard | The system flags repricing; you decide what it means |
| **FWDVOL & forward factor** | ~3 hours + worked examples | Jared's framework only pays off if you can read it intuitively |
| **Autocallables / structured product flows** | ~5 hours; harder topic | These cause the GEX dislocations the system will alert you to |
| **Dispersion & correlation** | ~3 hours | Single-stock vs index vol relationships matter for the Thrasher signal |
| **Bayesian updating** | Lifelong skill | The probability layer is literally Bayes; understanding it makes you a better user |
| **Polars / SQL fluency** | ~10 hours | When the dashboard isn't showing what you need, you'll write the query yourself |

Put one of these into `docs/learning/` per ~6 weeks. By Month 9 they're all closed.

---

## Risk events to plan for

These are the things that, if they happened, would derail the project. Have a plan for each.

| Risk | Probability | Plan |
|---|---|---|
| **ConvexValue raises prices or shuts down** | Low–Med | OptionsDataSource Protocol means Schwab/Barchart/Tradier can drop in. Parked Schwab `.env` is the emergency fallback |
| **Local Ollama quality is insufficient** | Med | Switch to Groq free tier (30 req/min, fine for daily AM summary) or Claude API |
| **Computer dies / data loss** | Low | Git + GitHub covers code. DB backup script (Phase 7) covers data. PDFs are in `data/pdfs/` — copy to external SSD weekly |
| **Mithil too busy to maintain weekly Convex login / system care** | Med | Build in a "system health" Discord ping that nags if any job hasn't run in 24h. Stops things rotting silently |
| **FlashAlpha rule turns out to be wrong** | Low (the research is solid) | The architecture already gates alerts behind strategy scanners, not raw Greek crossings. Even if FlashAlpha is wrong, the system isn't worse |
| **Trading interest fades** | Med (life happens) | Worst case: the system is a teaching tool. You'll have learned PostgreSQL, pgvector, Streamlit, APScheduler, RAG pipelines, and modern Python. That's transferable |

---

## What's intentionally NOT in scope

To avoid scope creep, here are things we are deliberately **not** building:

- **Trade execution.** Schwab credentials are parked but not used. The system tells you what's interesting; you push buttons.
- **Real-time tick data.** Snapshots every 5–30 min are enough. Tick-level is a different system (and a different budget).
- **Multi-user / login / billing.** Personal use only. If you ever go product, that's a separate fork.
- **Mobile app.** Streamlit + nginx HTTPS gives you a phone-readable web UI. Native app is overkill.
- **Crypto, FX, fixed income.** Equities + index options only. Scope discipline.
- **AI trade recommendations.** The system surfaces information and detects anomalies. It does NOT say "buy SPY". The FlashAlpha rule is in place precisely to prevent that.

---

## Three principles to revisit when stuck

1. **Regime descriptors, not signals.** Anything Greek-derived is a *description* of the dealer-positioning regime, not a trade trigger. Triggers come from validated strategies + the probability layer. (See CLAUDE.md Rule 4.)

2. **Local first, cheap forever.** Every architectural choice should default to free/local until there's clear evidence the upgrade pays off. The system should still run if you cancel every paid subscription except ConvexValue.

3. **Memory compounds.** The tagged observations, PDF chunks, AM summary history, and signals log are the durable asset. The code is replaceable. The data is not. Protect it (backups, schema discipline, no destructive migrations).

---

## Checkpoint cadence

Every 3 months, sit down with `MEMORY.md` open and answer:

1. **What did the system catch that I would have missed?** (1 example per week is enough.)
2. **What signal fires the most often, and is it real edge or noise?**
3. **What knowledge gap is most often blocking me from interpreting the dashboard?**
4. **Is the monthly cost still justified?**
5. **What's the single highest-leverage thing to build next?**

Write the answers into `docs/decisions/checkpoint-YYYY-Q?.md`. Three months later, read them.

---

*Document version: 1.0 — May 19, 2026*
*Update when: phase strategy shifts, major architectural change, year-end review*
