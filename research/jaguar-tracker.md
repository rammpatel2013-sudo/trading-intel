# Jaguar Analytics — Ongoing Knowledge Tracker

*Source: `alerts@jaguaranalytics.com` (Fahad Khalid). Seeded 2026-07-27 from Gmail history. Living doc — append new alerts/trades/flows/webinar mentions here. Descriptive research context only (FlashAlpha rule 4), NOT auto-signals.*

## How Jaguar publishes (where the full data lives)
Every "MPT, Research and Flows" email links four rolling files — bookmark/pull these for full detail:
- **MPT (Master Performance Tracker)** — `jaguaranalytics.com/mp-files/mpt-<n>.xlsx` — every official trade + status (source of truth for open/closed/PnL).
- **Research + Hidden Angles** — `research-<n>.xlsx` / `ha-<n>.pdf` — the idea pipeline.
- **JaguarFlow** — `flow-<n>.xlsx` — **history of all important options flows** (this is the "important flow" feed).
- **Webinar recordings** — permanent Vimeo link (bookmark once).
- **Quarterly Outlook + Premium Ideas** — decrypted into the repo (e.g. `Jaguar-3Q-2026-*`).

> Delivery note: many Jaguar emails arrive addressed to **santoshk04@gmail.com** but are in this (drmithil) mailbox — the letters pipeline filters by *sender*, so it catches them regardless of recipient.

---

## 1) Trade Alerts & MPT trades (the actual suggestions/trades)
| Date | Ticker | Dir | Structure / entry | Thesis / catalyst | Source |
|---|---|---|---|---|---|
| 2026-07-23 | **CRM** | Bear | Sep 165/145/130 skip-strike **put butterfly** $5.25 deb, 50% size | "Fading Moat"; macro risk-off (10Y >4.7%, Brent $105, Bab al-Mandab) | Trade Alert + MPT |
| 2026-07-21 | **SHOP** | Bull | (see recording) | "Strong Checks & Seasonality" | Trade Alert |
| 2026-07-20 | **MELI** | Bull | (see recording) | "3 Growth Engines" — 49% rev-growth disconnect | Trade Alert |
| 2026-07-02 | **QQQ** | Bear/hedge | Jul Wk 700/680/670 skip put fly $2.70 deb, half size ("shotgun") | Index hedge | MPT |
| 2026-02-24 | **STM** | Bull | Jun 35 calls @ $3.40 | — | MPT |
| 2026-02-17 | **GLNG** | Bull | (see recording) | "Strait of Hormuz" — Golar LNG | Trade Alert |
| 2026-02-11 | **SNDX** | Bull | (see recording) | "Pulmonary Disease" — Syndax, strong data | Trade Alert |
| 2026-01-05 | *(TBD)* | Bull | (see recording) | "Two Major Catalysts Ahead" | Trade Alert |
| 2025-12-11 | **IOT** | Bull | (see recording) | "Impressive Quarter" — Samsara | Trade Alert |
| 2025-12-04 | **MDT** | Bull | (see recording) | "Multiple Expansion Ahead" — Medtronic | Trade Alert |
| 2025-11-24 | **BIDU** | Bull | Buy stock + Apr 150 calls; tgt $150+, stop $107, 144d (px $119) | "Expect Big Rally in Q1" | Trade Alert |
| 2025-09-09 | **SWBI** | Bull | (see recording) | "Politically Charged" — gun makers | Trade Alert |

## 2) Special Situations & Event-Driven
| Date | Ticker | Read | Note | Our-data check |
|---|---|---|---|---|
| 2026-07-26 | **ARMK** | Bull | "Nexus is New" — Dec 60/65 calls; data-center services; MS ~700 target sites | ❌ sparse on our tape / not in coverage (add via `add_watchlist.py`) |
| 2026-06-28 | **DVN** | Bull | Screener "Above Ask Aggression" flow | our tape agrees DVN is sparse-flow (1 print/day clears $25k) |
| 2026-06-28 | **BLFS** | Event | Event-Driven Dashboard (wk Jun 21) — activist/M&A | — |

## 3) Weekend Research / Hidden Angles (idea pipeline)
| Date | Ticker | Read | Note |
|---|---|---|---|
| 2026-07-26 | **EBAY** | Bull | 94%-corr proprietary index → GMV upside; reports **Aug 5 AMC** |
| 2026-02-22 | **ATRO** | Bull | Astronics — post prelim results |
| 2025-11-24 | **W** | Bull | Wayfair — spending trends positive (Truist/BofA) |
| 2025-07-06 | **KTOS** | Bull | Kratos Defense — bouncing back from offering |

## 4) Quarterly Outlooks & Premium Ideas
| Date | Item | Repo artifact |
|---|---|---|
| 2026-06-28 | Jaguar **3Q26** Outlook & Premium Ideas | `Jaguar-3Q-2026-Ideas-Table.xlsx`, `...-Outlook-DECRYPTED.pdf`, `...-Summary.md` |
| 2025-12-21 | Jaguar **1Q26** Outlook & Premium Ideas | (to pull) |

## 5) Webinar Summaries (often contain trade mentions — extract on ingest)
2026-07-23 · 2026-07-21 · 2026-07-20 (8-hr, has a 6-page summary) · 2026-02-24 · 2026-01-27 · 2026-01-06. *Action: parse each for tickers/structures and fold into §1.*

## 6) Commentary / JaguarLive (daily macro + occasional ideas)
2026-07-06 "A Word with Myself" (post Iran-US MoU rotation) · JaguarLive dailies (Jul 14/15/16/21/22/23; Feb 23-25; Dec 22). *Skim for embedded Weekend-Research/idea callouts.*

---

## Patterns worth noting
- **Structure preference:** heavy use of **skip-strike put butterflies** as defined-risk index/single-name hedges (QQQ, CRM) — matches this week's "hedge into events, don't chase direction" regime our GEX data confirmed.
- **Flow-led ideas:** ARMK/DVN both originated from Jaguar's *flow* screeners (Above-Ask Aggression, repeat call buying) — these are the ones to cross-check against our `get_flow_intelligence` / `tas_prints`, but many are sub-$25k-notional names we don't capture.

## Maintenance
- **Auto:** Jaguar is already in the letters-pipeline `GMAIL_SENDERS` allowlist → once that pipeline is deployed, new Jaguar emails ingest to the research watchlist + knowledge automatically. This tracker is the human-readable ledger on top.
- **Manual add:** append a row to §1/§2/§3 per new Trade Alert / MPT change; pull `mpt-<n>.xlsx` + `flow-<n>.xlsx` for structure/flow detail.
- *Last updated: 2026-07-27.*
