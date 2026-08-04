"""Application configuration loaded from .env via pydantic-settings.

Single source of truth. Instantiate Settings() once at the composition root
(scheduler/runner.py or dashboard/Home.py) and inject downstream.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Anchor .env at the repo root (this file is trading_intel/config.py) so settings
# load no matter the current working directory. Critical for the MCP server, which
# Claude Desktop launches from its own cwd — a relative ".env" would not be found,
# and the required fields below would raise a startup ValidationError. Real env
# vars still take precedence over the file.
_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


class Settings(BaseSettings):
    """All app configuration, loaded from .env (or environment variables)."""

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ── App ────────────────────────────────────────────────────────────
    APP_ENV: Literal["development", "production"] = "development"
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    TZ: str = "America/New_York"

    # ── ConvexValue (PRIMARY data source) ──────────────────────────────
    CONVEX_EMAIL: str
    CONVEX_PASSWORD: SecretStr
    CONVEX_ACCOUNT_TYPE: Literal["pro", "live"] = "pro"

    # ── Local LLM via Ollama (free; replaces Anthropic + Voyage) ───────
    OLLAMA_HOST: str = "http://localhost:11434"
    LLM_DAILY_MODEL: str = "qwen2.5:14b"
    LLM_WEEKLY_MODEL: str = "qwen2.5:32b"
    LLM_TAGGING_MODEL: str = "qwen2.5:7b"
    EMBEDDING_MODEL: str = "nomic-embed-text"
    EMBEDDING_DIM: int = 768

    # ── Free data sources ──────────────────────────────────────────────
    FRED_API_KEY: SecretStr
    # FMP (company research: profile / financials / news; free tier ~250/day).
    FMP_API: SecretStr = SecretStr("")
    # SEC EDGAR fair-access requires a descriptive User-Agent incl. a contact email.
    EDGAR_USER_AGENT: str = "trading-intel research (set EDGAR_USER_AGENT in .env)"

    # ── CVForge (ConvexValue AI API — SECONDARY OptionsDataSource, ADR-004) ──
    # Same backend as convexlib, exposed as a keyed REST+MCP API. Research tier:
    # market-wide breadth (/screen, /query), historical option OHLC (/mas), and
    # 157 FMP endpoints. Used for breadth + history + FMP; convexlib stays PRIMARY
    # for the live regime engine (rule 1). Never logged (rule 2). Its /ai gateway
    # is out of scope — scheduled LLM stays on local Ollama (rule 7).
    CVFORGE_API_KEY: SecretStr = SecretStr("")
    CVFORGE_BASE_URL: str = "https://tap.convexvalue.com/api/data"

    # ── Discord webhooks (multiple channels) ───────────────────────────
    DISCORD_WEBHOOK_URL: SecretStr  # general / AM summary
    DISCORD_FLOW_WEBHOOK_URL: SecretStr = SecretStr("")
    DISCORD_IV_WEBHOOK_URL: SecretStr = SecretStr("")
    DISCORD_VEX_WEBHOOK_URL: SecretStr = SecretStr("")
    DISCORD_SIGNALS_WEBHOOK_URL: SecretStr = SecretStr("")
    DISCORD_INTERNALS_WEBHOOK_URL: SecretStr = SecretStr("")
    DISCORD_TRENDS_WEBHOOK_URL: SecretStr = SecretStr("")
    FLOW_ALERT_THRESHOLD: int = 10

    # ── Telegram delivery (bot push) + Gmail letters lane ──────────────
    TELEGRAM_BOT_TOKEN: SecretStr = SecretStr("")  # from @BotFather
    TELEGRAM_CHAT_ID: str = ""  # your chat id (getUpdates)
    GMAIL_CREDENTIALS_PATH: str = ""  # Google OAuth client credentials.json
    GMAIL_TOKEN_PATH: str = ""  # stored gmail.readonly token.json

    # ── Database ───────────────────────────────────────────────────────
    DATABASE_URL: str

    # ── Watchlist ──────────────────────────────────────────────────────
    # Single names only — index ETFs (SPY/QQQ/SPX) intentionally dropped: their
    # flow/regime is covered elsewhere and they dominate Convex API usage.
    WATCHLIST: str = "AAPL,MSFT,GOOGL,AMZN,META,NVDA,TSLA,AMD,SMCI,MRVL,ZS,BA,DIS"

    # ── Convex API rate limiting (vendor cap: 10 requests/min) ─────────
    # Per-process token-bucket cap in ConvexClient. Kept under 10 to leave
    # headroom for the always-on TAS daemon (~2/min) running concurrently.
    CONVEX_MAX_PER_MIN: int = 7

    # ── Intraday 0DTE/1DTE volume flow (DISABLED — was SPX/SPY/QQQ index) ─
    # Empty = delta_flow / intraday_flow no-op (index flow removed).
    INTRADAY_SYMBOLS: str = ""
    INTRADAY_STRIKE_RANGE: float = 0.03  # +/- fraction of spot for the tight 0DTE pull
    INTRADAY_MAX_DTE: int = 1  # keep 0DTE + 1DTE
    INTRADAY_RETENTION_HOURS: int = 48  # prune per-strike 5-min rows older than this

    # ── EOD wide chain (OI/flow change study) ──────────────────────────
    OI_CHAIN_WINDOW_DAYS: int = 180  # expirations within this DTE are stored
    OI_CHAIN_RETENTION_DAYS: int = 90  # prune oi_chain_eod rows older than this

    # ── Intraday live GEX (delta-band per-strike, pruned EOD) ──────────
    LIVE_GEX_SYMBOLS: str = ""  # comma list; empty -> effective watchlist (heavy)
    LIVE_GEX_STRIKE_RANGE: float = 0.10  # +/- fraction of spot for each pull
    LIVE_GEX_DELTA_LO: float = 0.30  # keep |delta| within [lo, hi] (near-the-money)
    LIVE_GEX_DELTA_HI: float = 0.70
    LIVE_GEX_RETENTION_HOURS: int = 24  # prune live_gex rows older than this

    # ── Daily chain snapshot breadth (feeds the GEX-surface all-expiry views) ──
    CHAIN_SNAPSHOT_MAX_EXPS: int = 40  # expirations to pull per daily snapshot (chain_long)
    CHAIN_SNAPSHOT_STRIKE_RANGE: float = 0.30  # +/- fraction of spot for the daily chain

    # ── Daily price history (quotes_daily backfill + EOD refresh) ──────
    QUOTES_BACKFILL_PERIOD: str = "5y"  # one-time history depth (yfinance period)
    QUOTES_REFRESH_PERIOD: str = "6mo"  # daily-job pull window (enough for rv60)

    # ── Options flow snapshots ────────────────────────────────────────
    FLOW_TOP_N: int = 10  # largest prints kept per snapshot
    FLOW_MIN_PACKAGE_PREMIUM: float = 250_000.0  # min $ premium for a notable package

    # ── Per-strike chain persistence excludes ─────────────────────────
    # Roots kept OUT of the heavy per-strike persisters (oi_chain_eod,
    # chain_snapshot) — and therefore out of skew/wall/vol-richness, which read
    # those tables. The aggregate greeks_snapshot job STILL collects net
    # GEX/DEX for these (index ETFs we only want the regime line for).
    CHAIN_EXCLUDE_ROOTS: str = "SPY,QQQ,SPX,SPXW"

    # ── Index roots always collected (aggregate GEX line + AM walls) ──────
    # SPX/SPY/QQQ are excluded from the heavy per-strike EOD persister
    # (CHAIN_EXCLUDE_ROOTS) and dropped from the single-name WATCHLIST — but the
    # daily brief needs their net-GEX flip series and morning dealer walls. The
    # greeks_snapshot job unions these in so the regime line never gaps when a
    # letter stops surfacing an index (this is why SPX's net-GEX went stale
    # Jun→Jul), and index_walls_am snapshots their per-strike chain once in the
    # AM (settled-overnight OI -> fresh walls without the storage of intraday).
    INDEX_ROOTS: str = "SPX,SPY,QQQ"

    # ── Constant-maturity forward IV (index ETFs) ─────────────────────
    # The index-ETF complement to skew_snapshots. These roots are excluded from
    # the per-strike persisters above, so the skew/surface pipeline has no chain
    # for them; the iv_tenor_snapshots job pulls a LIVE chain, builds the surface
    # in memory, and stores only ATM IV + the configured delta wings at fixed
    # constant-maturity tenors (no per-strike rows persisted).
    IV_TENOR_SYMBOLS: str = "QQQ,SPY,SPX"  # roots to snapshot (comma list)
    IV_TENOR_DTE: str = "30,90"  # constant-maturity tenors in calendar days (1M/3M)
    IV_TENOR_DELTAS: str = "15,25"  # wing |delta| points to store (ATM/50d always)

    # ── Options time & sales capture (Phase 3 NAS tape -> tas_prints) ──
    TAS_MIN_PREMIUM: float = 25_000.0  # keep prints with notional (price*size*100) >= this $
    TAS_LIMIT: int = 2000  # raw prints pulled per poll (indices flood the tape; pull wide)
    TAS_EXCLUDE_ROOTS: str = "SPY,QQQ,SPX,SPXW"  # high-volume index roots (covered by other jobs)
    TAS_POLL_INTERVAL: int = 30  # seconds between polls (NAS capture daemon)
    TAS_RETENTION_DAYS: int = 30  # prune raw tas_prints older than this
    TAS_INDEX_MIN_PREMIUM: float = 250_000.0  # un-exclude big index prints >= this $ (SPX/SPXW; ETFs use 0.4x)

    # ── Daily AM report ───────────────────────────────────────────────
    AM_REPORT_SEND_DISCORD: bool = False  # push AM report to Discord (client not built yet)

    # ── LETF net creation/redemption (issuance) flow ───────────
    # Leveraged/inverse ETFs snapshotted daily by scheduler/jobs/letf_flows.py.
    # FMP stable serves only the CURRENT shares figure, so dshares is banked
    # forward. Concentrated complexes feed the k(k-1)*assets*return EOD rebalance
    # estimate that sits beside GEX/DEX. Descriptive only (rule 4).
    LETF_SYMBOLS: str = (
        "TQQQ,SQQQ,SOXL,SOXS,SPXL,SPXU,TNA,TZA,FAS,FAZ,LABU,LABD,"
        "NUGT,DUST,JNUG,JDST,BOIL,KOLD,YINN,YANG,TSLL,TSLQ,NVDL,NVD"
    )

    # ── Factor scoring (ADR-005) ───────────────────────────────
    # Universe for the weekly multi-factor job (empty -> the watchlist).
    FACTOR_UNIVERSE: str = ""

    # ── Sentiment (institutional 13F + analyst ratings/targets) ─
    # Universe for the weekly sentiment snapshot job (empty -> the watchlist).
    SENTIMENT_UNIVERSE: str = ""

    # ── Per-name constant-maturity IV term (reads stored oi_chain_eod) ─
    # Tenors (calendar days) for the per-name IV-term curve; complements the
    # index-only iv_tenor job (watchlist universe).
    IV_TERM_DTE: str = "30,60,90"

    # ── Vol surface snapshots (full delta x expiry grid, index ETFs) ─
    # Roots to bank the whole surface for (the vol-surface-changes board); n
    # nearest liquid expiries kept per root.
    SURFACE_SYMBOLS: str = "SPX,QQQ,SPY"
    SURFACE_EXPIRIES: int = 12

    # ── Sector SPDR universe (sector lead/lag + fragility report) ──────
    # The 11 SPDR Select Sector ETFs. The sector layer is collected from
    # CVForge (secondary source, generous cap) — NEVER Convex — so it never
    # competes with the live regime engine for the 10/min Convex budget (rule 1).
    # Feeds scheduler/jobs/sector_greeks.py (per-SPDR net GEX/DEX/flip/ATM IV,
    # tagged source "cvforge" in greeks_snapshots) and the sector report.
    SECTOR_ROOTS: str = "XLB,XLC,XLE,XLF,XLI,XLK,XLP,XLRE,XLU,XLV,XLY"

    # ── EM-break / gamma burn-off + systematic flow (McGraw pattern) ───
    # See docs/em-break-system-plan.md + docs/learning/em-break-gamma-burnoff-digest.md.
    EARNINGS_LOOKAHEAD_DAYS: int = 30  # earn_cal pull window (days ahead)
    PRE_EARNINGS_SNAP_DAYS: int = 10  # snapshot the pre-earnings straddle within N days of a print
    PRE_EARNINGS_TARGET_DTE: int = 30  # prefer the ~30-DTE expiry bracketing the earnings date
    EM_BREAK_LOOKBACK_SESSIONS: int = 10  # post-earnings window for over-realization / re-entry
    # Systematic vol-control flow proxy (index-level tailwind). AUM/target are
    # ESTIMATES (flows/registry.py) — calibrate; consume flow $ as a percentile.
    VOL_CONTROL_INDEX: str = "SPX,QQQ"  # index roots the vol-control bid keys off
    VOL_CONTROL_AUM: float = 350e9  # overrides flows.registry vol_control default
    VOL_TARGET: float = 0.10  # target annualized vol (decimal)
    CTA_AUM: float = 300e9
    RISK_PARITY_AUM: float = 150e9
    RV_ROLLOFF_HORIZON: int = 10  # sessions to project the RV roll-off forward

    # ── Schwab (PARKED) ────────────────────────────────────────────────
    SCHWAB_APP_KEY: str = ""
    SCHWAB_APP_SECRET: SecretStr = SecretStr("")
    SCHWAB_CALLBACK_URL: str = "https://127.0.0.1"
    SCHWAB_TOKEN_PATH: str = "data/token.json"  # noqa: S105 (a file path, not a secret)

    @property
    def watchlist_symbols(self) -> list[str]:
        return [s.strip().upper() for s in self.WATCHLIST.split(",") if s.strip()]

    @property
    def chain_exclude_roots(self) -> set[str]:
        """Roots excluded from the per-strike chain persisters (set, upper)."""
        return {r.strip().upper() for r in self.CHAIN_EXCLUDE_ROOTS.split(",") if r.strip()}

    @property
    def index_roots(self) -> list[str]:
        """Index roots always collected for net GEX + AM walls (upper, de-duped)."""
        seen: set[str] = set()
        out: list[str] = []
        for tok in self.INDEX_ROOTS.split(","):
            s = tok.strip().upper()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
        return out

    @property
    def intraday_symbols(self) -> list[str]:
        return [s.strip().upper() for s in self.INTRADAY_SYMBOLS.split(",") if s.strip()]

    @property
    def iv_tenor_symbols(self) -> list[str]:
        """Index-ETF roots for the constant-maturity forward-IV job (set, upper)."""
        return [s.strip().upper() for s in self.IV_TENOR_SYMBOLS.split(",") if s.strip()]

    @property
    def iv_tenor_dtes(self) -> list[int]:
        """Constant-maturity tenors (calendar days), ascending and de-duplicated."""
        seen: set[int] = set()
        out: list[int] = []
        for tok in self.IV_TENOR_DTE.split(","):
            tok = tok.strip()
            if not tok:
                continue
            dte = int(tok)
            if dte not in seen:
                seen.add(dte)
                out.append(dte)
        return sorted(out)

    @property
    def iv_tenor_deltas(self) -> list[float]:
        """Wing |delta| points (percent) to store, ascending and de-duplicated."""
        seen: set[float] = set()
        out: list[float] = []
        for tok in self.IV_TENOR_DELTAS.split(","):
            tok = tok.strip()
            if not tok:
                continue
            d = float(tok)
            if d not in seen:
                seen.add(d)
                out.append(d)
        return sorted(out)

    @property
    def letf_symbols(self) -> list[str]:
        """LETF roots to snapshot for net-issuance flow (upper, de-duped, ordered)."""
        seen: set[str] = set()
        out: list[str] = []
        for tok in self.LETF_SYMBOLS.split(","):
            s = tok.strip().upper()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
        return out

    @property
    def factor_symbols(self) -> list[str]:
        """Universe for factor scoring (FACTOR_UNIVERSE, else the watchlist)."""
        syms = [s.strip().upper() for s in self.FACTOR_UNIVERSE.split(",") if s.strip()]
        return syms or self.watchlist_symbols

    @property
    def sentiment_symbols(self) -> list[str]:
        """Universe for sentiment snapshots (SENTIMENT_UNIVERSE, else the watchlist)."""
        syms = [s.strip().upper() for s in self.SENTIMENT_UNIVERSE.split(",") if s.strip()]
        return syms or self.watchlist_symbols

    @property
    def iv_term_dtes(self) -> list[int]:
        """Constant-maturity tenors (days) for the per-name IV-term job."""
        seen: set[int] = set()
        out: list[int] = []
        for tok in self.IV_TERM_DTE.split(","):
            tok = tok.strip()
            if not tok:
                continue
            dte = int(tok)
            if dte not in seen:
                seen.add(dte)
                out.append(dte)
        return sorted(out)

    @property
    def surface_symbols(self) -> list[str]:
        """Index-ETF roots for the full vol-surface snapshot (upper, de-duped)."""
        seen: set[str] = set()
        out: list[str] = []
        for tok in self.SURFACE_SYMBOLS.split(","):
            s = tok.strip().upper()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
        return out

    @property
    def sector_roots(self) -> list[str]:
        """The 11 sector SPDR roots for the sector lead/lag report (upper, de-duped)."""
        seen: set[str] = set()
        out: list[str] = []
        for tok in self.SECTOR_ROOTS.split(","):
            s = tok.strip().upper()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
        return out

    @property
    def vol_control_index_symbols(self) -> list[str]:
        """Index roots the systematic vol-control bid keys off (upper, de-duped)."""
        seen: set[str] = set()
        out: list[str] = []
        for tok in self.VOL_CONTROL_INDEX.split(","):
            s = tok.strip().upper()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
        return out


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
