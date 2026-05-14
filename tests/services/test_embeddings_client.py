"""Tests for the embeddings client module."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from app.services import embeddings_client as ec


@pytest.fixture(autouse=True)
def _reset_embeddings_singleton() -> None:
    """Reset the cached singleton before each test."""
    ec.reset_embeddings_client_singleton()


# ─────────────────────────────────────────────────────────────────────────────
# NoOpEmbeddingsClient
# ─────────────────────────────────────────────────────────────────────────────


class TestNoOpEmbeddingsClient:
    def test_deterministic_output(self) -> None:
        client = ec.NoOpEmbeddingsClient(dim=4)
        v1 = client.embed(["hello world"])
        v2 = client.embed(["hello world"])
        assert v1 == v2

    def test_different_inputs_different_vectors(self) -> None:
        client = ec.NoOpEmbeddingsClient(dim=4)
        v1 = client.embed(["hello"])
        v2 = client.embed(["world"])
        assert v1 != v2

    def test_dim_property(self) -> None:
        client = ec.NoOpEmbeddingsClient(dim=768)
        assert client.dim == 768
        assert len(client.embed(["test"])[0]) == 768

    def test_model_name(self) -> None:
        client = ec.NoOpEmbeddingsClient()
        assert client.model_name == "no-op"

    def test_multiple_texts(self) -> None:
        client = ec.NoOpEmbeddingsClient(dim=4)
        texts = ["a", "b", "c"]
        result = client.embed(texts)
        assert len(result) == 3
        assert all(len(v) == 4 for v in result)

    def test_protocol_conformance(self) -> None:
        client = ec.NoOpEmbeddingsClient()
        assert isinstance(client, ec.EmbeddingsClient)

    def test_empty_input(self) -> None:
        client = ec.NoOpEmbeddingsClient(dim=4)
        assert client.embed([]) == []

    def test_value_range(self) -> None:
        """Verify that all values are in [-1, 1]."""
        client = ec.NoOpEmbeddingsClient(dim=32)
        vec = client.embed(["some text"])[0]
        assert all(-1.0 <= v <= 1.0 for v in vec)


# ─────────────────────────────────────────────────────────────────────────────
# Factory — provider selection
# ─────────────────────────────────────────────────────────────────────────────


class TestProviderSelection:
    def test_no_provider_returns_none(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            assert ec.get_embeddings_client() is None

    def test_anthropic_provider_returns_none(self) -> None:
        with patch.dict(os.environ, {"LLM_PROVIDER": "anthropic"}, clear=True):
            assert ec.get_embeddings_client() is None

    def test_bedrock_provider_returns_none(self) -> None:
        with patch.dict(os.environ, {"LLM_PROVIDER": "bedrock"}, clear=True):
            assert ec.get_embeddings_client() is None

    def test_cli_providers_return_none(self) -> None:
        for provider in (
            "codex",
            "cursor",
            "claude-code",
            "gemini-cli",
            "opencode",
            "kimi",
            "copilot",
        ):
            ec.reset_embeddings_client_singleton()
            with patch.dict(os.environ, {"LLM_PROVIDER": provider}, clear=True):
                assert ec.get_embeddings_client() is None, f"{provider} should return None"

    def test_non_embedding_providers_return_none(self) -> None:
        for provider in ("openrouter", "requesty", "gemini", "nvidia", "minimax"):
            ec.reset_embeddings_client_singleton()
            with patch.dict(os.environ, {"LLM_PROVIDER": provider}, clear=True):
                assert ec.get_embeddings_client() is None, f"{provider} should return None"

    def test_openai_provider_no_key_returns_none(self) -> None:
        with (
            patch.dict(os.environ, {"LLM_PROVIDER": "openai"}, clear=True),
            patch("app.llm_credentials.resolve_llm_api_key", return_value=""),
        ):
            assert ec.get_embeddings_client() is None

    def test_openai_provider_with_key(self) -> None:
        with (
            patch.dict(os.environ, {"LLM_PROVIDER": "openai"}, clear=True),
            patch("app.llm_credentials.resolve_llm_api_key", return_value="sk-test"),
            patch("openai.OpenAI"),
        ):
            client = ec.get_embeddings_client()
            assert client is not None
            assert client.model_name == "text-embedding-3-small"
            assert client.dim == 1536

    def test_openai_embedding_model_large_dim(self) -> None:
        """text-embedding-3-large should report dim=3072."""
        assert ec._DEFAULT_EMBEDDINGS_DIMS["text-embedding-3-large"] == 3072

    def test_voyage_code_3_dim(self) -> None:
        """voyage-code-3 should report dim=2048."""
        assert ec._DEFAULT_EMBEDDINGS_DIMS["voyage-code-3"] == 2048

    def test_ollama_provider(self) -> None:
        with patch.dict(
            os.environ,
            {"LLM_PROVIDER": "ollama", "OLLAMA_HOST": "http://localhost:11434"},
            clear=True,
        ):
            client = ec.get_embeddings_client()
            assert client is not None
            assert isinstance(client, ec.OllamaEmbeddingsClient)
            assert client.model_name == "nomic-embed-text"
            assert client.dim == 768

    def test_voyage_provider_no_key_returns_none(self) -> None:
        with (
            patch.dict(os.environ, {"OPENSRE_EMBEDDINGS_PROVIDER": "voyage"}, clear=True),
            patch("app.llm_credentials.resolve_llm_api_key", return_value=""),
        ):
            assert ec.get_embeddings_client() is None

    def test_voyage_provider_with_key(self) -> None:
        with (
            patch.dict(os.environ, {"OPENSRE_EMBEDDINGS_PROVIDER": "voyage"}, clear=True),
            patch("app.llm_credentials.resolve_llm_api_key", return_value="vo-test-key"),
        ):
            client = ec.get_embeddings_client()
            assert client is not None
            assert isinstance(client, ec.VoyageEmbeddingsClient)
            assert client.model_name == "voyage-3-lite"
            assert client.dim == 512

    def test_opensre_embeddings_provider_overrides_llm_provider(self) -> None:
        """OPENSRE_EMBEDDINGS_PROVIDER takes precedence over LLM_PROVIDER."""
        with (
            patch.dict(
                os.environ,
                {
                    "OPENSRE_EMBEDDINGS_PROVIDER": "openai",
                    "LLM_PROVIDER": "anthropic",
                },
                clear=True,
            ),
            patch("app.llm_credentials.resolve_llm_api_key", return_value="sk-test"),
            patch("openai.OpenAI"),
        ):
            client = ec.get_embeddings_client()
            assert client is not None

    def test_singleton_caching(self) -> None:
        """Second call returns the same cached instance."""
        with (
            patch.dict(os.environ, {"LLM_PROVIDER": "openai"}, clear=True),
            patch("app.llm_credentials.resolve_llm_api_key", return_value="sk-test"),
            patch("openai.OpenAI"),
        ):
            c1 = ec.get_embeddings_client()
            c2 = ec.get_embeddings_client()
            assert c1 is c2

    def test_singleton_caches_none(self) -> None:
        """None result is also cached."""
        with patch.dict(os.environ, {"LLM_PROVIDER": "anthropic"}, clear=True):
            assert ec.get_embeddings_client() is None
            assert ec.get_embeddings_client() is None

    def test_reset_singleton(self) -> None:
        """After reset, a new call creates a fresh instance."""
        with (
            patch.dict(os.environ, {"LLM_PROVIDER": "openai"}, clear=True),
            patch("app.llm_credentials.resolve_llm_api_key", return_value="sk-test"),
            patch("openai.OpenAI"),
        ):
            c1 = ec.get_embeddings_client()
            ec.reset_embeddings_client_singleton()
            c2 = ec.get_embeddings_client()
            assert c1 is not c2

    def test_opensre_embeddings_model_override(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "LLM_PROVIDER": "openai",
                    "OPENSRE_EMBEDDINGS_MODEL": "text-embedding-3-large",
                },
                clear=True,
            ),
            patch("app.llm_credentials.resolve_llm_api_key", return_value="sk-test"),
            patch("openai.OpenAI"),
        ):
            client = ec.get_embeddings_client()
            assert client is not None
            assert client.model_name == "text-embedding-3-large"


# ─────────────────────────────────────────────────────────────────────────────
# OpenAIEmbeddingsClient — happy path
# ─────────────────────────────────────────────────────────────────────────────


class TestOpenAIEmbeddingsHappyPath:
    def test_batched_embed(self) -> None:
        fake_embeddings = MagicMock()
        fake_embeddings.create.return_value = MagicMock(
            data=[
                MagicMock(index=0, embedding=[0.1, 0.2]),
                MagicMock(index=1, embedding=[0.3, 0.4]),
            ]
        )

        fake_openai = MagicMock()
        fake_openai.return_value.embeddings = fake_embeddings

        with patch("openai.OpenAI", fake_openai):
            client = ec.OpenAIEmbeddingsClient(model="text-embedding-3-small", api_key="sk-test")
            result = client.embed(["hello", "world"])

        assert len(result) == 2
        assert result[0] == [0.1, 0.2]
        assert result[1] == [0.3, 0.4]


# ─────────────────────────────────────────────────────────────────────────────
# OllamaEmbeddingsClient — happy path
# ─────────────────────────────────────────────────────────────────────────────


class TestOllamaEmbeddingsHappyPath:
    def test_single_text(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {"embeddings": [[0.5, 0.6]]}
        mock_response.raise_for_status.return_value = None

        with patch(
            "app.services.embeddings_client.httpx.post", return_value=mock_response
        ) as mock_post:
            client = ec.OllamaEmbeddingsClient(
                model="nomic-embed-text", host="http://localhost:11434"
            )
            result = client.embed(["test"])

        assert result == [[0.5, 0.6]]
        mock_post.assert_called_once_with(
            "http://localhost:11434/api/embed",
            json={"model": "nomic-embed-text", "input": ["test"]},
            timeout=30.0,
        )

    def test_batched_texts(self) -> None:
        """Multiple texts are sent in a single HTTP request."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "embeddings": [[0.1, 0.2], [0.3, 0.4]]
        }
        mock_response.raise_for_status.return_value = None

        with patch(
            "app.services.embeddings_client.httpx.post", return_value=mock_response
        ) as mock_post:
            client = ec.OllamaEmbeddingsClient(
                model="nomic-embed-text", host="http://localhost:11434"
            )
            result = client.embed(["hello", "world"])

        assert result == [[0.1, 0.2], [0.3, 0.4]]
        mock_post.assert_called_once_with(
            "http://localhost:11434/api/embed",
            json={"model": "nomic-embed-text", "input": ["hello", "world"]},
            timeout=30.0,
        )

    def test_empty_input(self) -> None:
        client = ec.OllamaEmbeddingsClient(model="nomic-embed-text")
        assert client.embed([]) == []


# ─────────────────────────────────────────────────────────────────────────────
# VoyageEmbeddingsClient — happy path
# ─────────────────────────────────────────────────────────────────────────────


class TestVoyageEmbeddingsHappyPath:
    def test_embed(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {"index": 0, "embedding": [0.7, 0.8]},
                {"index": 1, "embedding": [0.9, 1.0]},
            ]
        }
        mock_response.raise_for_status.return_value = None

        with patch(
            "app.services.embeddings_client.httpx.post", return_value=mock_response
        ) as mock_post:
            client = ec.VoyageEmbeddingsClient(model="voyage-3-lite", api_key="vo-test")
            result = client.embed(["a", "b"])

        assert result == [[0.7, 0.8], [0.9, 1.0]]
        mock_post.assert_called_once_with(
            "https://api.voyageai.com/v1/embeddings",
            headers={"Authorization": "Bearer vo-test"},
            json={"input": ["a", "b"], "model": "voyage-3-lite"},
            timeout=30.0,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Protocol conformance
# ─────────────────────────────────────────────────────────────────────────────


class TestProtocolConformance:
    def test_openai_conforms(self) -> None:
        with patch("openai.OpenAI"):
            client = ec.OpenAIEmbeddingsClient(model="text-embedding-3-small", api_key="sk-test")
            assert isinstance(client, ec.EmbeddingsClient)

    def test_voyage_conforms(self) -> None:
        client = ec.VoyageEmbeddingsClient(model="voyage-3-lite", api_key="vo-test")
        assert isinstance(client, ec.EmbeddingsClient)

    def test_ollama_conforms(self) -> None:
        client = ec.OllamaEmbeddingsClient(model="nomic-embed-text")
        assert isinstance(client, ec.EmbeddingsClient)
