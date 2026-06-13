"""Embeddings client wrapper and factory."""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Protocol, cast

import httpx
from openai import OpenAI

from app.config import resolve_llm_settings
from app.llm_credentials import resolve_llm_api_key

logger = logging.getLogger(__name__)

_MODEL_DIMS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
    "voyage-3-lite": 512,
    "voyage-3": 1024,
    "voyage-2": 1024,
    "nomic-embed-text": 768,
}


class EmbeddingsClient(Protocol):
    """Protocol for embedding clients."""

    @property
    def model_name(self) -> str:
        """The model name used for embeddings."""
        ...

    @property
    def dim(self) -> int:
        """The dimensionality of the embedding vectors."""
        ...

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for the given list of texts."""
        ...


class OpenAIEmbeddingsClient:
    """Embeddings client for OpenAI (or compatible) providers."""

    def __init__(self, model_name: str, api_key: str, base_url: str | None = None) -> None:
        self._model_name = model_name
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._dim = _MODEL_DIMS.get(model_name, 1536)

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            response = self._client.embeddings.create(
                input=texts,
                model=self._model_name,
            )
            return [item.embedding for item in response.data]
        except Exception as e:
            logger.error("[embeddings] OpenAI embed call failed: %s", e)
            raise RuntimeError(f"OpenAI embeddings call failed: {e}") from e


class VoyageEmbeddingsClient:
    """Embeddings client for Voyage AI."""

    def __init__(self, model_name: str, api_key: str) -> None:
        self._model_name = model_name
        self._api_key = api_key
        self._dim = _MODEL_DIMS.get(model_name, 512)

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            with httpx.Client(timeout=60.0) as client:
                res = client.post(
                    "https://api.voyageai.com/v1/embeddings",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "input": texts,
                        "model": self._model_name,
                    },
                )
                res.raise_for_status()
                data = res.json()
                return [item["embedding"] for item in data["data"]]
        except Exception as e:
            logger.error("[embeddings] Voyage embed call failed: %s", e)
            raise RuntimeError(f"Voyage embeddings call failed: {e}") from e


class OllamaEmbeddingsClient:
    """Embeddings client for local Ollama service."""

    def __init__(self, model_name: str, host: str) -> None:
        self._model_name = model_name
        self._host = host.rstrip("/")
        self._dim = _MODEL_DIMS.get(model_name, 768)

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            with httpx.Client(timeout=60.0) as client:
                res = client.post(
                    f"{self._host}/api/embed",
                    json={
                        "model": self._model_name,
                        "input": texts,
                    },
                )
                res.raise_for_status()
                data = res.json()
                return cast("list[list[float]]", data["embeddings"])
        except Exception as e:
            logger.error("[embeddings] Ollama embed call failed: %s", e)
            raise RuntimeError(f"Ollama embeddings call failed: {e}") from e


class _NoOpEmbeddingsClient:
    """Deterministic no-op embeddings client for tests."""

    def __init__(self, model_name: str = "noop-embedding", dim: int = 1536) -> None:
        self._model_name = model_name
        self._dim = dim

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        results = []
        for text in texts:
            vector = []
            for i in range(self._dim):
                h = hashlib.sha256(f"{text}:{i}".encode()).digest()
                val = int.from_bytes(h[:4], "big") / 0xFFFFFFFF
                vector.append(val * 2.0 - 1.0)
            results.append(vector)
        return results


def get_embeddings_client() -> EmbeddingsClient | None:
    """Factory to get the configured embeddings client, or None if not configured."""
    model_override = os.getenv("OPENSRE_EMBEDDINGS_MODEL")

    provider: str
    try:
        settings = resolve_llm_settings()
        provider = settings.provider
    except Exception:
        provider = os.getenv("LLM_PROVIDER", "anthropic").strip().lower()

    if provider == "openai":
        default_model = "text-embedding-3-small"
    elif provider == "ollama":
        default_model = "nomic-embed-text"
    elif provider in ("anthropic", "bedrock", "voyage"):
        default_model = "voyage-3-lite"
    else:
        default_model = "text-embedding-3-small"

    model_name = model_override or default_model

    if "voyage" in model_name or provider in ("anthropic", "bedrock", "voyage"):
        voyage_key = resolve_llm_api_key("VOYAGE_API_KEY") or os.getenv("VOYAGE_API_KEY")
        if voyage_key:
            return VoyageEmbeddingsClient(model_name, voyage_key)

    if "nomic" in model_name or provider == "ollama":
        try:
            settings = resolve_llm_settings()
            host = settings.ollama_host
        except Exception:
            host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
        return OllamaEmbeddingsClient(model_name, host)

    openai_key = (
        resolve_llm_api_key("OPENAI_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or (settings.openai_api_key if "settings" in locals() else None)
    )
    if openai_key:
        return OpenAIEmbeddingsClient(model_name, openai_key)

    return None
