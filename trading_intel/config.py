"""Application configuration loaded from .env via pydantic-settings.

Single source of truth for all environment-derived values. Importable from
anywhere in the codebase, but instantiate Settings() once at the composition
root (scheduler/runner.py or dashboard/Home.py) and inject downstream.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All app configuration, loaded from .env (or environment variables)."""

    model_config = SettingsConfigDict(
        env_file=".env",
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

    # ── Anthropic ──────────────────────────────────────────────────────
    ANTHROPIC_API_KEY: SecretStr
    CLAUDE_DAILY_MODEL: str = "claude-sonnet-4-6"
    CLAUDE_WEEKLY_MODEL: str = "claude-opus-4-6"

    # ── Voyage embeddings ──────────────────────────────────────────────
    VOYAGE_API_KEY: SecretStr
    VOYAGE_MODEL: str = "voyage-3"

    # ── Free data sources ──────────────────────────────────────────────
    FRED_API_KEY: SecretStr

    # ── Discord ────────────────────────────────────────────────────────
    DISCORD_WEBHOOK_URL: SecretStr

    # ── Database ───────────────────────────────────────────────────────
    DATABASE_URL: str

    # ── Watchlist ──────────────────────────────────────────────────────
    WATCHLIST: str = "SPY,QQQ,SPX,AAPL,MSFT,GOOGL,AMZN,META,NVDA,TSLA,AMD,SMCI,PLTR"

    # ── Schwab (PARKED) ────────────────────────────────────────────────
    SCHWAB_APP_KEY: str = ""
    SCHWAB_APP_SECRET: SecretStr = SecretStr("")
    SCHWAB_CALLBACK_URL: str = "https://127.0.0.1"
    SCHWAB_TOKEN_PATH: str = "data/token.json"

    # ── Optional future vendors ────────────────────────────────────────
    BARCHART_API_KEY: SecretStr = SecretStr("")
    TRADIER_TOKEN: SecretStr = SecretStr("")

    @property
    def watchlist_symbols(self) -> list[str]:
        return [s.strip().upper() for s in self.WATCHLIST.split(",") if s.strip()]


# Lazy singleton — callers should `from trading_intel.config import get_settings`
# rather than module-level instantiation, to keep import side-effects clean.
_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
