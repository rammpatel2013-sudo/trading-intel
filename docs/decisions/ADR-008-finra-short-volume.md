# ADR-008: FINRA daily short-volume file as a data source (own DIX)

- Date: 2026-11-13 (overnight build; Mithil kept Phase 6 "FINRA short-volume collector" in the 11/13 backlog cut)
- Status: accepted
- Context: SqueezeMetrics publishes DIX free (already collected, `squeezemetrics_daily`), but one vendor's
  number with no visibility into the method. FINRA posts the raw Reg SHO short volume for every symbol daily,
  free, no key (`cdn.finra.org/equity/regsho/daily/CNMSshvolYYYYMMDD.txt`).
- Decision: add `trading_intel/clients/finra.py` (the only module that talks to FINRA) and the nightly
  `finra_dix` job computing a dollar-weighted short-volume share for S&P 500 names into `finra_dix_daily`.
  No paid vendor, no key, one HTTP GET a day (+ ~6 FMP batch-quote calls on the existing CVForge key).
- Consequences: a second DIX to compare with SqueezeMetrics; descriptor only (rule 4). Drop if the two
  disagree without explanation after ~40 sessions.
