"""Pytest configuration and shared fixtures."""
import os

import pytest


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch):
    """Provide stub values for any env vars that pydantic-settings requires."""
    stubs = {
        "CONVEX_EMAIL": "ci@example.com",
        "CONVEX_PASSWORD": "ci-stub",
        "OLLAMA_HOST": "http://localhost:11434",
        "FRED_API_KEY": "ci-stub",
        "DISCORD_WEBHOOK_URL": "https://example.com/webhook",
        "DATABASE_URL": "postgresql+psycopg://intel:intel@localhost:5432/trading_intel",
    }
    for k, v in stubs.items():
        monkeypatch.setenv(k, v)
    yield
