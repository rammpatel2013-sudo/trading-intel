"""Application configuration loaded from .env via pydantic-settings.

Single source of truth. Instantiate Settings() once at the composition root
(scheduler/runner.py or dashboard/Home.py) and inject downstream.
"""
from __future__ import annotations

from typing import Literal

from pydantic import SecretStr
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

    # ── Local LLM via Ollama (free; replaces Anthropic + Voyage) ───────
    OLLAMA_HOST: str = "http://localhost:11434"
    LLM_DAILY_MODEL: str = "qwen2.5:14b"
    LLM_WEEKLY_MODEL: str = "qwen2.5:32b"
    LLM_TAGGING_MODEL: str = "qwen2.5:7b"
    EMBEDDING_MODEL: str = "nomic-embed-text"
    EMBEDDING_DIM: int = 768

    # ── Free data sources ──────────────────────────────────────────────
    FRED_API_KEY: SecretStr

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
    WATCHLIST: str = "SPY,QQQ,SPX,AAPL,MSFT,GOOGL,AMZN,META,NVDA,TSLA,AMD,SMCI,PLTR"

    # ── Schwab (PARKED) ────────────────────────────────────────────────
    SCHWAB_APP_KEY: str = ""
    SCHWAB_APP_SECRET: SecretStr = SecretStr("")
    SCHWAB_CALLBACK_URL: str = "https://127.0.0.1"
    SCHWAB_TOKEN_PATH: str = "data/token.json"

    @property
    def watchlist_symbols(self) -> list[str]:
        return [s.strip().upper() for s in self.WATCHLIST.split(",") if s.strip()]


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
