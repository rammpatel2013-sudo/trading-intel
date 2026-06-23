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

    # ── Discord webhooks (multiple channels) ───────────────────────────
    DISCORD_WEBHOOK_URL: SecretStr                       # general / AM summary
    DISCORD_FLOW_WEBHOOK_URL: SecretStr = SecretStr("")
    DISCORD_IV_WEBHOOK_URL: SecretStr = SecretStr("")
    DISCORD_VEX_WEBHOOK_URL: SecretStr = SecretStr("")
    DISCORD_SIGNALS_WEBHOOK_URL: SecretStr = SecretStr("")
    DISCORD_INTERNALS_WEBHOOK_URL: SecretStr = SecretStr("")
    DISCORD_TRENDS_WEBHOOK_URL: SecretStr = SecretStr("")
    FLOW_ALERT_THRESHOLD: int = 10

    # ── Database ───────────────────────────────────────────────────────
    DATABASE_URL: str

    # ── Watchlist ──────────────────────────────────────────────────────
    # Single names only — index ETFs (SPY/QQQ/SPX) intentionally dropped: their
    # flow/regime is covered elsewhere and they dominate Convex API usage.
    WATCHLIST: str = "AAPL,MSFT,GOOGL,AMZN,META,NVDA,AMD,SMCI,MRVL,ZS,BA,DIS"

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

    # ── Options time & sales capture (Phase 3 NAS tape -> tas_prints) ──
    TAS_MIN_PREMIUM: float = 25_000.0  # keep prints with notional (price*size*100) >= this $
    TAS_LIMIT: int = 2000  # raw prints pulled per poll (indices flood the tape; pull wide)
    TAS_EXCLUDE_ROOTS: str = "SPY,QQQ,SPX,SPXW"  # high-volume index roots (covered by other jobs)
    TAS_POLL_INTERVAL: int = 30  # seconds between polls (NAS capture daemon)
    TAS_RETENTION_DAYS: int = 30  # prune raw tas_prints older than this

    # ── Daily AM report ───────────────────────────────────────────────
    AM_REPORT_SEND_DISCORD: bool = False  # push AM report to Discord (client not built yet)

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
    def intraday_symbols(self) -> list[str]:
        return [s.strip().upper() for s in self.INTRADAY_SYMBOLS.split(",") if s.strip()]


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
