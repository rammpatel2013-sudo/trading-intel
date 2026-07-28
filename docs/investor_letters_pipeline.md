# Investor-letters ingestion pipeline — design

> **STATUS 2026-07-19: BUILT (all lanes + Telegram/email delivery), pending deploy.**
> Deploy checklist: `docs/DEPLOY_NEXT_SESSION_letters_research.md`. Planning is complete.

*Drafted 2026-07-19. Source list + fetch config: `Investor_Letters_Tracker.xlsx`
(in the "stock analysis" project). This doc is the plan for the automation the user
asked for: track sources -> pull new letters -> digest -> update the **research**
watchlist + graphify knowledge -> hand back a summary. Nothing built yet — approve the
increment order first.*

## Gmail lane — the PRIMARY source (added 2026-07-19)

An inbox survey showed the letters already arrive by **email** — richer than scraping
public sites, and it reaches the LP-only / direct-email funds no scraper can. So the
Gmail connector (search + attachment download) is the primary `letters_fetch` lane: pull
by a **sender allowlist**, extract the body + PDF attachments, then the same ingest ->
watchlist -> report. Runs unattended via the Gmail API on the NAS. (They arrive via
Unroll.me + inbox with no clean investment label, so an allowlist is the reliable key.)

**Sender allowlist** (from the survey — extend as new senders appear):

*Direct fund letters (often PDF attachments):*
- Meditation Capital — `tim@meditationcapital.com`
- Gator Financial Partners — `investorrelations@gatorcapital.com`
- Deep Sail Capital — `info@deepsailcapital.com`
- Rhizome Hard Asset Opportunity Fund — `hardasset2023@gmail.com`

*Research services (rich, PDF attachments — high value):*
- Special Situations Research (SSR) — `info@specialsitsresearch.com`, `specialsitsresearch@gmail.com`  ← "the situation guy"
- Jaguar Analytics — `alerts@jaguaranalytics.com`  ← flow / trade ideas + quarterly Outlook + special situations

*Aggregators (pre-compile many funds' pitches — highest coverage):*
- Hedge Fund Best Ideas / Stock Analysis Compilation — `hfbestideas@substack.com`
- Buyside Digest — `info@buysidedigest.com`  (features Hinde, Myrmikan, RGA…)
- The Acquirer's Multiple — `johnny@acquirersmultiple.com`

*Value letters / essays:*
- Onveston `onveston@substack.com` · Quick Value `vdl@substack.com` · Phenom Capital
  `phenomcapital@substack.com` · Eliant Capital `eliantcap@substack.com` · IMA / Katsenelson
  `vk@imausa1.com` · Best Anchor Stocks · Uncover Alpha `uncoveralpha@substack.com` · The Generalist.

*Vol / options (already system inputs):*
- Doc McGraw `docmcgraw@substack.com` · Jared H Stocks `jaredhstocks@substack.com` ·
  Gamma Report `kurtsaltrichter+gamma-report@substack.com` · Yamco (`yamtrades`).

**Build shape:** `GMAIL_SENDERS` in `letters/sources.py`; a Gmail lane in `letters_fetch`
that queries `from:(<allowlist>) newer_than:<since>`, saves each body + attachment under
`research/company/letters/<sender>/`, then calls the existing `ingest_folder`. PDF
attachments already flow through `extract_text`. High-value senders (SSR, Jaguar,
Meditation, Gator) attach PDFs — the attachment path matters most for them.

## The one hard constraint

There is no unified feed for ~55 heterogeneous funds. From the tracker they split into
lanes, and only some are cleanly automatable:

- **Substack (RSS)** — cleanest. Alluvial, Eagle Point, Voss, Icaria, Greystone, Hinde
  (+ Praetorian's blog). A `/feed` URL per fund; poll and diff.
- **SEC EDGAR (structured, reliable)** — the 13F/LP-only and activist names (Nantahala,
  Makaira, TowerView, Mill Road, Minerva, Peter Kamin, Cannell, Radoff…). No letter to
  read, but holdings (13F) and activist 13D/Form 4 are pullable by CIK via EDGAR's
  submissions API. This is how you "read" the LP-only funds.
- **Website scrape (per-site fragility)** — Upslope, Vulcan, Fairholme, SVN, Choice,
  River Oaks, Sohra Peak, Azvalor, Magallanes, Gate City… each a bespoke letters page.
- **Aggregators / gated / intl** — Roubaix, Oakcliff, ShawSpring (Seeking Alpha / MOI /
  Insider Monkey), Bossert/Glenorchy (login), Torsan (CNMV). Manual or skip.

So "fully automate all 60" isn't realistic; "automate Substack + EDGAR now, scrape the
top public sites next, leave the gated ones manual" is.

## How it maps to the stack (CLAUDE.md rules)

- **Data-source isolation (rule 1):** letters/filings are a *new* input, orthogonal to
  ConvexValue — a dedicated `trading_intel/letters/` package (fetchers behind a small
  `LetterSource` Protocol), never reaching around Convex. Sources come from the tracker
  exported to a checked-in config, not hard-coded.
- **Local LLM only in scheduled paths (rule 7):** the digest/extraction step uses the
  existing `LLMProvider` Protocol on **local Ollama** (`LLM_TAGGING_MODEL` for
  ticker/section tagging, `LLM_DAILY_MODEL` for the prose summary). No cloud LLM in the
  job. Deep ad-hoc reading stays in Claude Desktop via MCP.
- **Watchlist safety (MEMORY `watchlist-junk-tickers`):** extracted tickers go to the
  **research watchlist**, never the active options watchlist — these micro-caps mostly
  have no liquid options and would bank blank regime rows. Surfaced via
  `get_research_watchlist`.
- **Knowledge (MEMORY `graphify-knowledge-setup`):** letter text/PDF flows into the
  existing graphify PDF-ingestion path on the NAS (chunk tagging via
  `LLM_TAGGING_MODEL`), so letters join the knowledge graph alongside the playbooks.
- **Idempotency + schema (rules 5, 3):** every letter/filing tracked by content hash in
  new tables (`investor_letters`, `letter_digests`, `filing_holdings`) via Alembic
  migrations; re-runs skip already-ingested items.

## Stages

1. **Fetch** (`letters_fetch` job): read the sources config → pull new letters
   (Substack RSS, then site scrape) into `data/letters/<fund>/` + a row in
   `investor_letters` (fund, date, title, url, hash, path, status).
2. **Filings** (`filings_fetch` job): for EDGAR-lane funds, pull latest 13F/13D by CIK,
   diff vs prior quarter → `filing_holdings` (new / added / trimmed / exited).
3. **Digest** (`letters_digest` job): local-Ollama over each new letter →
   `{summary, tickers[], stated_buys/sells, key_quotes}` into `letter_digests`.
4. **Watchlist update:** resolve digest + filing tickers → symbols → upsert into the
   **research** watchlist (dedup; no optionability gate).
5. **Knowledge:** stage letter PDFs into the graphify ingest folder on the NAS.
6. **Summary:** a rolling "what got digested" report (the HTML report pattern) — new
   letters, new theses, new tickers added, notable 13F moves — optionally weekly to
   Discord.

## Proposed build increments (approve an order)

- **Increment 1 — Substack + digest + research watchlist + summary.** Highest ROI,
  lowest fragility. ~6 funds' RSS → local-Ollama digest → research watchlist → a summary
  report. Proves the whole spine end-to-end.
- **Increment 2 — SEC EDGAR lane.** 13F holdings diff for the LP-only names + 13D/Form 4
  for the activists. Structured and reliable; this is how the ~25 no-letter funds get
  covered.
- **Increment 3 — Website-scrape lane.** The top ~12 public-letter sites (Upslope,
  Vulcan, Fairholme, SVN, Choice, River Oaks, Azvalor, Magallanes, Gate City, Sohra
  Peak, Long Cast, DKAM). Per-site parsers, added incrementally.
- **Increment 4 — Knowledge + scheduling.** graphify ingestion wiring + weekly summary +
  NAS DSM tasks.

## Open choices for you

- **Where letters live:** `trading-intel/data/letters/` (co-located with the pipeline +
  watchlist + graphify) vs the "stock analysis" project. Recommend trading-intel, since
  that's where the watchlist and graphify already are.
- **Summary delivery:** HTML report only, or also a weekly Discord push.
- **Start point:** recommend **Increment 1** (Substack spine) — smallest, proves the
  end-to-end flow, immediately useful.
