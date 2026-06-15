"""Provider-backed embeddings clients for retrieval-style features."""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Callable
from typing import Any, Protocol

import httpx
from openai import OpenAI

from app.config import DEFAULT_OLLAMA_HOST, get_configured_llm_provider, load_env
from app.llm_credentials import resolve_llm_api_key

logger = logging.getLogger(__name__)

EMBEDDINGS_PROVIDER_ENV = "OPENSRE_EMBEDDINGS_PROVIDER"
EMBEDDINGS_MODEL_ENV = "OPENSRE_EMBEDDINGS_MODEL"
OPENAI_EMBEDDINGS_MODEL = "text-embedding-3-small"
OPENAI_EMBEDDINGS_DIM = 1536
OLLAMA_EMBEDDINGS_MODEL = "nomic-embed-text"
OLLAMA_EMBEDDINGS_DIM = 768
VOYAGE_EMBEDDINGS_MODEL = "voyage-3-lite"
VOYAGE_EMBEDDINGS_DIM = 512


class EmbeddingsClient(Protocol):
    """Minimal synchronous embeddings interface used by retrieval callers."""

    @property
    def model_name(self) -> str:
        """Embedding model identifier used by this client."""
        ...

    @property
    def dim(self) -> int:
        """Embedding vector dimension, or the last observed dimension."""
        ...

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of input strings in input order."""
        ...


class _NoOpEmbeddingsClient:
    """Deterministic local embeddings for tests and offline fallback paths."""

    def __init__(self, *, dim: int = 16, model_name: str = "noop-hash-embedding") -> None:
        if dim <= 0:
            raise ValueError("dim must be greater than zero")
        self._dim = dim
        self._model_name = model_name

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [_hash_embedding(text, self._dim) for text in texts]


def _hash_embedding(text: str, dim: int) -> list[float]:
    values: list[float] = []
    counter = 0
    payload = text.encode("utf-8")
    while len(values) < dim:
        digest = hashlib.sha256(counter.to_bytes(4, "big") + payload).digest()
        values.extend((byte / 127.5) - 1.0 for byte in digest)
        counter += 1
    return values[:dim]


NoOpEmbeddingsClient = _NoOpEmbeddingsClient


OpenAIClientFactory = Callable[[str, str | None], Any]


class OpenAIEmbeddingsClient:
    """Embeddings client for OpenAI and OpenAI-compatible APIs."""

    def __init__(
        self,
        *,
        model: str,
        api_key_env: str = "OPENAI_API_KEY",
        api_key_default: str = "",
        base_url: str | None = None,
        dim: int = 0,
        client_factory: OpenAIClientFactory | None = None,
    ) -> None:
        self._model_name = model
        self._api_key_env = api_key_env
        self._api_key_default = api_key_default
        self._base_url = base_url
        self._dim = dim
        self._client_factory = client_factory or _build_openai_client

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        api_key = resolve_llm_api_key(self._api_key_env) or self._api_key_default
        if not api_key:
            raise RuntimeError(
                f"Missing {self._api_key_env}. Set it in your environment, .env, "
                "or secure local keychain before running embeddings steps."
            )

        client: Any | None = None
        vectors: list[list[float]]
        try:
            client = self._client_factory(api_key, self._base_url)
            response = client.embeddings.create(model=self._model_name, input=texts)
            vectors = [list(item.embedding) for item in response.data]
        except Exception as exc:
            logger.exception("OpenAI-compatible embeddings request failed.")
            raise RuntimeError(
                f"Embeddings request failed for model {self._model_name!r}."
            ) from exc
        finally:
            _close_client(client)

        self._record_dim(vectors, expected_count=len(texts))
        return vectors

    def _record_dim(self, vectors: list[list[float]], *, expected_count: int) -> None:
        _ensure_vector_count(vectors, expected_count=expected_count)
        if vectors:
            self._dim = len(vectors[0])


def _build_openai_client(api_key: str, base_url: str | None) -> OpenAI:
    return OpenAI(api_key=api_key, base_url=base_url, timeout=30.0)


def _close_client(client: Any | None) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        close()


HttpClientFactory = Callable[[], Any]


class VoyageEmbeddingsClient:
    """Embeddings client for Voyage AI's HTTP API."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        api_key_env: str = "VOYAGE_API_KEY",
        dim: int = VOYAGE_EMBEDDINGS_DIM,
        http_client_factory: HttpClientFactory | None = None,
    ) -> None:
        self._api_key = api_key
        self._api_key_env = api_key_env
        self._model_name = model
        self._dim = dim
        self._http_client_factory = http_client_factory or _build_http_client

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        api_key = self._api_key or resolve_llm_api_key(self._api_key_env)
        if not api_key:
            raise RuntimeError(
                f"Missing {self._api_key_env}. Set it in your environment, .env, "
                "or secure local keychain before running embeddings steps."
            )

        client: Any | None = None
        vectors: list[list[float]]
        try:
            client = self._http_client_factory()
            response = client.post(
                "https://api.voyageai.com/v1/embeddings",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": self._model_name, "input": texts},
            )
            response.raise_for_status()
            payload = response.json()
            vectors = [list(item["embedding"]) for item in payload.get("data", [])]
        except Exception as exc:
            logger.exception("Voyage embeddings request failed.")
            raise RuntimeError(
                f"Embeddings request failed for model {self._model_name!r}."
            ) from exc
        finally:
            _close_client(client)

        _ensure_vector_count(vectors, expected_count=len(texts))
        if vectors:
            self._dim = len(vectors[0])
        return vectors


def _build_http_client() -> httpx.Client:
    return httpx.Client(timeout=30.0)


def _ensure_vector_count(vectors: list[list[float]], *, expected_count: int) -> None:
    if len(vectors) != expected_count:
        raise RuntimeError(
            f"Embeddings response returned {len(vectors)} vectors for {expected_count} input texts."
        )


def get_embeddings_client() -> EmbeddingsClient | None:
    """Return an embeddings client for the active provider, or ``None`` when unavailable."""

    provider = _get_configured_embeddings_provider()
    model_override = os.getenv(EMBEDDINGS_MODEL_ENV, "").strip()
    if provider == "openai":
        if not resolve_llm_api_key("OPENAI_API_KEY"):
            logger.info("OpenAI embeddings disabled because OPENAI_API_KEY is not configured.")
            return None
        return OpenAIEmbeddingsClient(
            model=model_override or OPENAI_EMBEDDINGS_MODEL,
            dim=OPENAI_EMBEDDINGS_DIM,
        )
    if provider == "voyage":
        api_key = resolve_llm_api_key("VOYAGE_API_KEY")
        if not api_key:
            logger.info("Voyage embeddings disabled because VOYAGE_API_KEY is not configured.")
            return None
        return VoyageEmbeddingsClient(
            api_key=api_key,
            model=model_override or VOYAGE_EMBEDDINGS_MODEL,
            dim=VOYAGE_EMBEDDINGS_DIM,
        )
    if provider == "ollama":
        host = os.getenv("OLLAMA_HOST", DEFAULT_OLLAMA_HOST).strip() or DEFAULT_OLLAMA_HOST
        return OpenAIEmbeddingsClient(
            model=model_override or OLLAMA_EMBEDDINGS_MODEL,
            api_key_env="OLLAMA_API_KEY",
            api_key_default="ollama",
            base_url=f"{host.rstrip('/')}/v1",
            dim=OLLAMA_EMBEDDINGS_DIM,
        )
    logger.debug("No embeddings client is available for embeddings provider %s.", provider)
    return None


def _get_configured_embeddings_provider() -> str:
    load_env(override=False)
    provider = os.getenv(EMBEDDINGS_PROVIDER_ENV, "").strip().lower()
    return provider or get_configured_llm_provider()


__all__ = [
    "EmbeddingsClient",
    "NoOpEmbeddingsClient",
    "OpenAIEmbeddingsClient",
    "VoyageEmbeddingsClient",
    "get_embeddings_client",
]
