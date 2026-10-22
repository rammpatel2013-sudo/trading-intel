"""LLM provider abstraction.

The `LLMProvider` Protocol is the contract every LLM vendor must satisfy.
Code in `synthesis/`, `memory/` (tagging), etc. depends on this Protocol
— NOT on `OllamaProvider` directly. This keeps vendor swap-out cheap (Ollama
today, Anthropic API tomorrow if budget allows).
"""
from __future__ import annotations

from typing import Protocol

import ollama

from trading_intel.config import Settings


class LLMProvider(Protocol):
    """Contract for any LLM vendor (Ollama, Anthropic, OpenAI, etc.)."""

    def complete(self, prompt: str, *, model: str | None = None, max_tokens: int = 2048) -> str:
        """Single-turn text completion."""
        ...

    def complete_json(self, prompt: str, *, model: str | None = None, max_tokens: int = 2048) -> str:
        """Completion constrained to JSON where the backend supports it (default: plain ``complete``)."""
        return self.complete(prompt, model=model, max_tokens=max_tokens)

    def chat(self, messages: list[dict], *, model: str | None = None, max_tokens: int = 2048) -> str:
        """Multi-turn chat completion. messages = [{'role': 'user'|'assistant'|'system', 'content': str}]"""
        ...

    def embed(self, text: str | list[str], *, model: str | None = None) -> list[list[float]]:
        """Return embedding vector(s). Single string → 1 vector. List → many."""
        ...


class OllamaProvider(LLMProvider):
    """Local LLM via Ollama. Default provider — free, no API keys needed."""

    def __init__(self, settings: Settings):
        self._client = ollama.Client(host=settings.OLLAMA_HOST)
        self._default_model = settings.LLM_DAILY_MODEL
        self._embedding_model = settings.EMBEDDING_MODEL

    def complete(self, prompt: str, *, model: str | None = None, max_tokens: int = 2048) -> str:
        response = self._client.generate(
            model=model or self._default_model,
            prompt=prompt,
            options={"num_predict": max_tokens},
        )
        return response["response"]

    def complete_json(self, prompt: str, *, model: str | None = None, max_tokens: int = 2048) -> str:
        # Ollama's grammar-constrained JSON mode: the model cannot answer in prose.
        # Doc's 14.5K-char letter made the tagging model return a summary instead
        # of the schema for a month (2026-09-21 → 10-22); ``format="json"`` ends that.
        response = self._client.generate(
            model=model or self._default_model,
            prompt=prompt,
            format="json",
            options={"num_predict": max_tokens, "temperature": 0},
        )
        return response["response"]

    def chat(
        self,
        messages: list[dict],
        *,
        model: str | None = None,
        max_tokens: int = 2048,
    ) -> str:
        response = self._client.chat(
            model=model or self._default_model,
            messages=messages,
            options={"num_predict": max_tokens},
        )
        return response["message"]["content"]

    def embed(self, text: str | list[str], *, model: str | None = None) -> list[list[float]]:
        texts = [text] if isinstance(text, str) else text
        m = model or self._embedding_model
        return [self._client.embeddings(model=m, prompt=t)["embedding"] for t in texts]
