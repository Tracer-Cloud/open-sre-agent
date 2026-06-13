"""Tests for EmbeddingsClient implementations and factory."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from app.services.embeddings_client import (
    OllamaEmbeddingsClient,
    OpenAIEmbeddingsClient,
    VoyageEmbeddingsClient,
    _NoOpEmbeddingsClient,
    get_embeddings_client,
)


def test_noop_embeddings_client() -> None:
    client = _NoOpEmbeddingsClient(model_name="noop", dim=4)
    assert client.model_name == "noop"
    assert client.dim == 4

    res = client.embed(["hello", "world"])
    assert len(res) == 2
    assert len(res[0]) == 4
    assert len(res[1]) == 4
    # Deterministic vectors
    res2 = client.embed(["hello", "world"])
    assert res == res2


def test_openai_embeddings_client() -> None:
    mock_response = MagicMock()
    mock_item1 = MagicMock()
    mock_item1.embedding = [0.1, 0.2]
    mock_item2 = MagicMock()
    mock_item2.embedding = [0.3, 0.4]
    mock_response.data = [mock_item1, mock_item2]

    mock_openai = MagicMock()
    mock_openai.embeddings.create.return_value = mock_response

    with patch("app.services.embeddings_client.OpenAI", return_value=mock_openai):
        client = OpenAIEmbeddingsClient(model_name="text-embedding-3-small", api_key="test-key")
        assert client.model_name == "text-embedding-3-small"
        assert client.dim == 1536

        res = client.embed(["text1", "text2"])
        assert res == [[0.1, 0.2], [0.3, 0.4]]
        mock_openai.embeddings.create.assert_called_once_with(
            input=["text1", "text2"],
            model="text-embedding-3-small",
        )


def test_voyage_embeddings_client() -> None:
    mock_res_json = {"data": [{"embedding": [0.5, 0.6]}, {"embedding": [0.7, 0.8]}]}

    mock_response = MagicMock()
    mock_response.json.return_value = mock_res_json
    mock_response.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.post.return_value = mock_response

    with patch("app.services.embeddings_client.httpx.Client") as mock_client_cls:
        mock_client_cls.return_value.__enter__.return_value = mock_client
        client = VoyageEmbeddingsClient(model_name="voyage-3-lite", api_key="test-key")
        assert client.model_name == "voyage-3-lite"
        assert client.dim == 512

        res = client.embed(["text1", "text2"])
        assert res == [[0.5, 0.6], [0.7, 0.8]]
        mock_client.post.assert_called_once_with(
            "https://api.voyageai.com/v1/embeddings",
            headers={
                "Authorization": "Bearer test-key",
                "Content-Type": "application/json",
            },
            json={
                "input": ["text1", "text2"],
                "model": "voyage-3-lite",
            },
        )


def test_ollama_embeddings_client() -> None:
    mock_res_json = {"embeddings": [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]}
    mock_response = MagicMock()
    mock_response.json.return_value = mock_res_json
    mock_response.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.post.return_value = mock_response

    with patch("app.services.embeddings_client.httpx.Client") as mock_client_cls:
        mock_client_cls.return_value.__enter__.return_value = mock_client
        client = OllamaEmbeddingsClient(
            model_name="nomic-embed-text", host="http://localhost:11434"
        )
        assert client.model_name == "nomic-embed-text"
        assert client.dim == 768

        res = client.embed(["text1", "text2"])
        assert res == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
        mock_client.post.assert_called_once_with(
            "http://localhost:11434/api/embed",
            json={
                "model": "nomic-embed-text",
                "input": ["text1", "text2"],
            },
        )


def test_factory_resolves_correctly() -> None:
    # 1. Override with OPENSRE_EMBEDDINGS_MODEL
    with (
        patch.dict(
            os.environ, {"OPENSRE_EMBEDDINGS_MODEL": "voyage-3", "VOYAGE_API_KEY": "voyagekey"}
        ),
        patch(
            "app.services.embeddings_client.resolve_llm_settings",
            side_effect=Exception("no settings"),
        ),
    ):
        client = get_embeddings_client()
        assert isinstance(client, VoyageEmbeddingsClient)
        assert client.model_name == "voyage-3"

    # 2. OpenAI provider
    mock_settings = MagicMock()
    mock_settings.provider = "openai"
    mock_settings.openai_api_key = "openaikey"
    with (
        patch.dict(os.environ, {}, clear=True),
        patch("app.services.embeddings_client.resolve_llm_settings", return_value=mock_settings),
        patch("app.services.embeddings_client.resolve_llm_api_key", return_value="openaikey"),
    ):
        client = get_embeddings_client()
        assert isinstance(client, OpenAIEmbeddingsClient)
        assert client.model_name == "text-embedding-3-small"

    # 3. None when no keys
    with (
        patch.dict(os.environ, {}, clear=True),
        patch(
            "app.services.embeddings_client.resolve_llm_settings",
            side_effect=Exception("no settings"),
        ),
        patch("app.services.embeddings_client.resolve_llm_api_key", return_value=None),
    ):
        client = get_embeddings_client()
        assert client is None
