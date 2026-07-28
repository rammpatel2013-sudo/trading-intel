# NEXT SESSION — deploy the letters → research → delivery pipeline

Everything below is BUILT this session (planning done). Next session = **laptop verify →
GitHub push → NAS image rebuild → DSM tasks**. Source of truth for the design:
`docs/investor_letters_pipeline.md`, `docs/ticker_research_report_plan.md`.

## The full chain (all idempotent NAS jobs)

| # | Job | Does | Delivery |
|---|-----|------|----------|
| 1 | `letters_fetch` | **Gmail lane** (sender allowlist → body + PDF attachments) + Substack → ingest → research watchlist + knowledge | — |
| 2 | `filings_fetch` | 13F via CVForge FMP → `filing_holdings` → QoQ diff → watchlist | — |
| 3 | `research_report` | per research ticker: CVForge stage (4h/day/wk) + FMP fundamentals/institutional/analyst + transcript + letter commentary → `reports/<SYM>_research.html` | — |
| 4 | `letters_digest` | weekly HTML digest of new watchlist names → **Telegram** (text + doc) + email | Telegram + email |

New code: `letters/gmail_source.py`, `letters/sources.py` (`GMAIL_SENDERS`),
`clients/telegram.py`, `research/{stage,enrich}.py`, jobs `research_report` + `letters_digest`
(+ `letters_fetch`/`filings_fetch` from earlier). Migration 0039.

## One-time setup (do once, before scheduling)

**A. Config fields** — add to `trading_intel/config.py` `Settings` + `.env.template` (empty), real values in `.env` (rule 2):
```
TELEGRAM_BOT_TOKEN=      # from @BotFather
TELEGRAM_CHAT_ID=        # your chat id
GMAIL_CREDENTIALS_PATH=  # path to Google OAuth client credentials.json
GMAIL_TOKEN_PATH=        # path to the stored gmail.readonly token.json
```

**B. Telegram bot** — message `@BotFather` → `/newbot` → copy the token. Send your bot any
message, then open `https://api.telegram.org/bot<token>/getUpdates` and read
`result[].message.chat.id`. Put both in `.env`.

**C. Gmail API (read-only)** — pip deps + OAuth:
```
pip install google-api-python-client google-auth google-auth-oauthlib
```
Create an OAuth client (Desktop) in Google Cloud → download `credentials.json`; set
`GMAIL_CREDENTIALS_PATH` + `GMAIL_TOKEN_PATH` in `.env`; then run **`python scripts/gmail_auth.py`**
(one-off browser consent) to mint `token.json` with scope `gmail.readonly`.
`gmail_source.fetch_new` degrades to a no-op until the token is present, so nothing
breaks meanwhile.

## Laptop verification

```powershell
cd C:\Users\drmit\PycharmProjects\trading-intel
.venv\Scripts\activate
pytest -q tests\letters tests\research tests\backtest      # new suites green
ruff check trading_intel\letters trading_intel\research trading_intel\clients\telegram.py trading_intel\scheduler\jobs
black --check trading_intel\letters trading_intel\research trading_intel\clients\telegram.py
alembic upgrade head ; alembic downgrade -1 ; alembic upgrade head   # 0039 round-trip
# smoke each job once (needs CVForge key / Gmail token / Telegram token as set up above):
python -m trading_intel.scheduler.jobs.filings_fetch
python -m trading_intel.scheduler.jobs.letters_fetch
python -m trading_intel.scheduler.jobs.research_report TAP
python -m trading_intel.scheduler.jobs.letters_digest
```
(The mount-lag in this build env means the pure logic was verified by direct execution;
the laptop `pytest` is the real confirmation.)

## GitHub push + NAS image

```powershell
git add -A ; git commit -m "letters→research→delivery pipeline (gmail + telegram + reports)" ; git push origin main
```
```bash
ssh drmithil@192.168.1.211
sudo sh -c 'curl -L https://codeload.github.com/rammpatel2013-sudo/trading-intel/tar.gz/refs/heads/main -o /tmp/ti.tgz && tar xzf /tmp/ti.tgz -C /tmp && cp -r /tmp/trading-intel-main/. /var/services/homes/drmithil/trading-intel/ && /usr/local/bin/docker build --no-cache -t trading-intel:latest /var/services/homes/drmithil/trading-intel'
```
(Copy `.env`, `credentials.json`, `token.json` onto the NAS too — they're gitignored.)

## DSM tasks (User: root; ET) — `run_job.sh`

```
letters_fetch    weekly Mon 07:30
filings_fetch    weekly Mon 07:45
research_report  weekly Mon 08:15   (after 1+2 so the watchlist is fresh)
letters_digest   weekly Mon 08:45   (after the reports; pushes to Telegram)
```

## Verify
```bash
tail -20 ~/ti_letters_fetch.log ~/ti_filings_fetch.log ~/ti_research_report.log ~/ti_letters_digest.log
ls -lt /var/services/homes/drmithil/trading-intel/reports/*_research_*.html | head
```
Telegram should receive the digest text + the HTML on the first `letters_digest` run.

## Also queued (from earlier, batch into the same image rebuild)
EM-break (0037/3 tasks), factor_scores, iv_term, surface, skew-backfill — see their DEPLOY docs.
