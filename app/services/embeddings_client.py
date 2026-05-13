"""Embeddings client with provider factory and graceful no-op fallback.

Provides a ``EmbeddingsClient`` protocol with a factory function
:func:`get_embeddings_client` that returns a provider-specific
implementation or ``None`` when no provider/credentials are configured.
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Protocol, runtime_checkable

import httpx

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_EMBEDDINGS_MODELS: dict[str, str] = {
    "openai": "text-embedding-3-small",
    "voyage": "voyage-3-lite",
    "ollama": "nomic-embed-text",
}

_DEFAULT_EMBEDDINGS_DIMS: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "voyage-3-lite": 1024,
    "nomic-embed-text": 768,
}

# Embedding providers that are derived from ``LLM_PROVIDER``.
_LLM_PROVIDER_TO_EMBEDDINGS_PROVIDER: dict[str, str] = {
    "openai": "openai",
    "ollama": "ollama",
}

# Providers that don't support embeddings — return None from the factory.
_NON_EMBEDDING_PROVIDERS: frozenset[str] = frozenset(
    {
        "anthropic",
        "openrouter",
        "requesty",
        "gemini",
        "nvidia",
        "minimax",
        "bedrock",
        "codex",
        "cursor",
        "claude-code",
        "gemini-cli",
        "opencode",
        "kimi",
        "copilot",
    }
)

_EMBEDDINGS_TIMEOUT_SEC = 30.0


# ─────────────────────────────────────────────────────────────────────────────
# Protocol
# ─────────────────────────────────────────────────────────────────────────────


@runtime_checkable
class EmbeddingsClient(Protocol):
    """Protocol for embedding providers.

    Implementations must provide ``embed``, ``model_name``, and ``dim``.
    """

    model_name: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of texts into a list of vectors."""
        ...


# ─────────────────────────────────────────────────────────────────────────────
# Provider implementations
# ─────────────────────────────────────────────────────────────────────────────


class OpenAIEmbeddingsClient:
    """Embeddings client backed by the OpenAI API."""

    def __init__(self, *, model: str, api_key: str) -> None:
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key, timeout=_EMBEDDINGS_TIMEOUT_SEC)
        self.model_name = model
        self.dim = _DEFAULT_EMBEDDINGS_DIMS.get(model, 1536)

    def embed(self, texts: list[str]) -> list[list[float]]:
        response = self._client.embeddings.create(model=self.model_name, input=texts)
        response.data.sort(key=lambda d: d.index)
        return [d.embedding for d in response.data]


class VoyageEmbeddingsClient:
    """Embeddings client backed by the Voyage AI API (via httpx)."""

    BASE_URL = "https://api.voyageai.com/v1/embeddings"

    def __init__(self, *, model: str, api_key: str) -> None:
        self.model_name = model
        self.dim = _DEFAULT_EMBEDDINGS_DIMS.get(model, 1024)
        self._api_key = api_key

    def embed(self, texts: list[str]) -> list[list[float]]:
        response = httpx.post(
            self.BASE_URL,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={"input": texts, "model": self.model_name},
            timeout=_EMBEDDINGS_TIMEOUT_SEC,
        )
        response.raise_for_status()
        data = response.json()
        data["data"].sort(key=lambda d: d["index"])
        return [d["embedding"] for d in data["data"]]


class OllamaEmbeddingsClient:
    """Embeddings client backed by a local Ollama instance."""

    def __init__(self, *, model: str, host: str = "http://localhost:11434") -> None:
        self.model_name = model
        self.dim = _DEFAULT_EMBEDDINGS_DIMS.get(model, 768)
        self._base_url = host.rstrip("/")

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            response = httpx.post(
                f"{self._base_url}/api/embed",
                json={"model": self.model_name, "input": text},
                timeout=_EMBEDDINGS_TIMEOUT_SEC,
            )
            response.raise_for_status()
            data = response.json()
            vectors.append(data["embeddings"][0])
        return vectors


# ─────────────────────────────────────────────────────────────────────────────
# No-op / test client
# ─────────────────────────────────────────────────────────────────────────────


class NoOpEmbeddingsClient:
    """Deterministic embeddings client for tests.

    Returns vectors derived from a hash of each input text so that the
    output is predictable across runs.
    """

    def __init__(self, dim: int = 768) -> None:
        self.model_name = "no-op"
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        result: list[list[float]] = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            # Expand the 32-byte digest to ``dim`` floats in [-1, 1].
            floats_from_bytes = [(digest[i % 32] / 127.5) - 1.0 for i in range(self.dim)]
            result.append(floats_from_bytes)
        return result


# ─────────────────────────────────────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────────────────────────────────────


def get_embeddings_client() -> EmbeddingsClient | None:
    """Resolve and return an :class:`EmbeddingsClient`, or ``None``.

    Resolution order:

    1. ``OPENSRE_EMBEDDINGS_PROVIDER`` env var (when set).
    2. ``LLM_PROVIDER`` env var — only providers in
       :data:`_LLM_PROVIDER_TO_EMBEDDINGS_PROVIDER` produce a client.
    3. Returns ``None`` when no suitable provider is configured.

    The embedding model can be overridden via ``OPENSRE_EMBEDDINGS_MODEL``
    — if unset, a sensible per-provider default is chosen
    (:data:`_DEFAULT_EMBEDDINGS_MODELS`).

    Returns ``None`` (silent no-op) for CLI-backed LLM providers or when
    credentials are missing, so callers can skip RAG features gracefully.
    """
    raw_provider = (
        (os.getenv("OPENSRE_EMBEDDINGS_PROVIDER", "") or os.getenv("LLM_PROVIDER", "") or "")
        .strip()
        .lower()
    )

    if not raw_provider or raw_provider in _NON_EMBEDDING_PROVIDERS:
        return None

    # Map LLM_PROVIDER to the actual embeddings provider.
    provider = _LLM_PROVIDER_TO_EMBEDDINGS_PROVIDER.get(raw_provider, raw_provider)

    model_override = os.getenv("OPENSRE_EMBEDDINGS_MODEL", "").strip()
    model = model_override or _DEFAULT_EMBEDDINGS_MODELS.get(provider, "text-embedding-3-small")

    if provider == "openai":
        from app.llm_credentials import resolve_llm_api_key

        api_key = resolve_llm_api_key("OPENAI_API_KEY")
        if not api_key:
            logger.warning("OpenAI API key not found; embeddings disabled.")
            return None
        return OpenAIEmbeddingsClient(model=model, api_key=api_key)

    if provider == "voyage":
        from app.llm_credentials import resolve_llm_api_key

        api_key = resolve_llm_api_key("VOYAGE_API_KEY")
        if not api_key:
            logger.warning("Voyage API key not found; embeddings disabled.")
            return None
        return VoyageEmbeddingsClient(model=model, api_key=api_key)

    if provider == "ollama":
        host = os.getenv("OLLAMA_HOST", "http://localhost:11434").strip()
        return OllamaEmbeddingsClient(model=model, host=host)

    return None
