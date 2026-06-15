from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.embeddings_client import (
    EmbeddingsClient,
    NoOpEmbeddingsClient,
    OpenAIEmbeddingsClient,
    VoyageEmbeddingsClient,
    get_embeddings_client,
)


class _FakeOpenAIEmbeddings:
    def __init__(self, vectors: list[list[float]]) -> None:
        self._vectors = vectors
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(data=[SimpleNamespace(embedding=vector) for vector in self._vectors])


class _FakeOpenAIClient:
    def __init__(self, vectors: list[list[float]]) -> None:
        self.embeddings = _FakeOpenAIEmbeddings(vectors)
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeVoyageResponse:
    def __init__(self, vectors: list[list[float]]) -> None:
        self._vectors = vectors

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {"data": [{"embedding": vector} for vector in self._vectors]}


class _FakeHttpClient:
    def __init__(self, vectors: list[list[float]]) -> None:
        self._response = _FakeVoyageResponse(vectors)
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    def post(self, url: str, **kwargs: Any) -> _FakeVoyageResponse:
        self.calls.append({"url": url, **kwargs})
        return self._response

    def close(self) -> None:
        self.closed = True


def _skip_env_load(monkeypatch: pytest.MonkeyPatch) -> None:
    def _load_env(*, override: bool) -> None:
        assert override is False

    monkeypatch.setattr("app.services.embeddings_client.load_env", _load_env)


def test_noop_embeddings_are_deterministic_and_batched() -> None:
    client: EmbeddingsClient = NoOpEmbeddingsClient(dim=6)

    first = client.embed(["same", "different", "same"])
    second = client.embed(["same", "different", "same"])

    assert first == second
    assert first[0] == first[2]
    assert first[0] != first[1]
    assert len(first) == 3
    assert all(len(vector) == client.dim for vector in first)


@pytest.mark.parametrize("dim", [0, -1])
def test_noop_embeddings_reject_non_positive_dim(dim: int) -> None:
    with pytest.raises(ValueError, match="dim must be greater than zero"):
        NoOpEmbeddingsClient(dim=dim)


def test_factory_returns_none_for_missing_openai_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _skip_env_load(monkeypatch)
    monkeypatch.delenv("OPENSRE_EMBEDDINGS_PROVIDER", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("app.services.embeddings_client.resolve_llm_api_key", lambda _env: "")

    assert get_embeddings_client() is None


def test_factory_selects_openai_with_model_override(monkeypatch: pytest.MonkeyPatch) -> None:
    _skip_env_load(monkeypatch)
    monkeypatch.delenv("OPENSRE_EMBEDDINGS_PROVIDER", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENSRE_EMBEDDINGS_MODEL", "text-embedding-test")
    monkeypatch.setattr(
        "app.services.embeddings_client.resolve_llm_api_key", lambda _env: "sk-test"
    )

    client = get_embeddings_client()

    assert isinstance(client, OpenAIEmbeddingsClient)
    assert client.model_name == "text-embedding-test"
    assert client.dim == 1536


def test_factory_loads_env_and_selects_voyage_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("OPENSRE_EMBEDDINGS_PROVIDER", raising=False)
    monkeypatch.delenv("OPENSRE_EMBEDDINGS_MODEL", raising=False)

    def _load_env(*, override: bool) -> None:
        assert override is False
        monkeypatch.setenv("OPENSRE_EMBEDDINGS_PROVIDER", "voyage")
        monkeypatch.setenv("OPENSRE_EMBEDDINGS_MODEL", "voyage-env-model")

    monkeypatch.setattr("app.services.embeddings_client.load_env", _load_env)
    monkeypatch.setattr(
        "app.services.embeddings_client.resolve_llm_api_key",
        lambda env: "pa-test" if env == "VOYAGE_API_KEY" else "sk-test",
    )

    client = get_embeddings_client()

    assert isinstance(client, VoyageEmbeddingsClient)
    assert client.model_name == "voyage-env-model"
    assert client.dim == 512


def test_factory_selects_ollama_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    _skip_env_load(monkeypatch)
    monkeypatch.delenv("OPENSRE_EMBEDDINGS_PROVIDER", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")
    monkeypatch.setattr("app.services.embeddings_client.resolve_llm_api_key", lambda _env: "")

    client = get_embeddings_client()

    assert isinstance(client, OpenAIEmbeddingsClient)
    assert client.model_name == "nomic-embed-text"
    assert client.dim == 768


def test_factory_logs_unsupported_provider_at_debug(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _skip_env_load(monkeypatch)
    monkeypatch.delenv("OPENSRE_EMBEDDINGS_PROVIDER", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    caplog.set_level(logging.INFO, logger="app.services.embeddings_client")

    assert get_embeddings_client() is None
    assert "No embeddings client is available" not in caplog.text


def test_openai_embed_sends_batch_and_tracks_response_dim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FakeOpenAIClient([[0.1, 0.2], [0.3, 0.4]])
    monkeypatch.setattr(
        "app.services.embeddings_client.resolve_llm_api_key", lambda _env: "sk-test"
    )

    client = OpenAIEmbeddingsClient(
        model="custom-embedding",
        client_factory=lambda _api_key, _base_url: fake_client,
    )

    vectors = client.embed(["alpha", "beta"])

    assert vectors == [[0.1, 0.2], [0.3, 0.4]]
    assert client.dim == 2
    assert fake_client.closed is True
    assert fake_client.embeddings.calls == [
        {"model": "custom-embedding", "input": ["alpha", "beta"]},
    ]


def test_embedding_clients_return_empty_batch_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.services.embeddings_client.resolve_llm_api_key", lambda _env: "")

    openai_client = OpenAIEmbeddingsClient(
        model="custom-embedding",
        client_factory=lambda _api_key, _base_url: pytest.fail("client should not be built"),
    )
    voyage_client = VoyageEmbeddingsClient(
        api_key="",
        model="voyage-test",
        http_client_factory=lambda: pytest.fail("client should not be built"),
    )

    assert openai_client.embed([]) == []
    assert voyage_client.embed([]) == []


def test_openai_embed_rejects_mismatched_vector_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FakeOpenAIClient([[0.1, 0.2]])
    monkeypatch.setattr(
        "app.services.embeddings_client.resolve_llm_api_key", lambda _env: "sk-test"
    )
    client = OpenAIEmbeddingsClient(
        model="custom-embedding",
        client_factory=lambda _api_key, _base_url: fake_client,
    )

    with pytest.raises(RuntimeError, match="returned 1 vectors for 2 input texts"):
        client.embed(["alpha", "beta"])


def test_voyage_embed_sends_batch_and_tracks_response_dim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_http = _FakeHttpClient([[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]])
    monkeypatch.setattr(
        "app.services.embeddings_client.resolve_llm_api_key", lambda _env: "pa-test"
    )
    client = VoyageEmbeddingsClient(
        api_key="pa-test",
        model="voyage-test",
        http_client_factory=lambda: fake_http,
    )

    vectors = client.embed(["alpha", "beta"])

    assert vectors == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    assert client.dim == 3
    assert fake_http.closed is True
    assert fake_http.calls == [
        {
            "url": "https://api.voyageai.com/v1/embeddings",
            "headers": {"Authorization": "Bearer pa-test"},
            "json": {"model": "voyage-test", "input": ["alpha", "beta"]},
        }
    ]


def test_voyage_embed_prefers_constructor_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_http = _FakeHttpClient([[0.1]])
    monkeypatch.setattr(
        "app.services.embeddings_client.resolve_llm_api_key", lambda _env: "env-key"
    )
    client = VoyageEmbeddingsClient(
        api_key="explicit-key",
        model="voyage-test",
        http_client_factory=lambda: fake_http,
    )

    assert client.embed(["alpha"]) == [[0.1]]
    assert fake_http.calls[0]["headers"] == {"Authorization": "Bearer explicit-key"}
