"""Azure OpenAI LiteLLM routing tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.llm.transports.litellm.clients import LiteLLMAgentClient, LiteLLMLLMClient
from core.llm.transports.litellm.routing import build_litellm_agent_client, build_litellm_llm_client


def _azure_settings() -> SimpleNamespace:
    return SimpleNamespace(
        provider="azure-openai",
        azure_openai_base_url="https://example.openai.azure.com/",
        azure_openai_api_version="2024-10-21",
        azure_openai_reasoning_model="gpt-5.4-mini",
        azure_openai_classification_model="gpt-5.4-mini",
        azure_openai_toolcall_model="gpt-5.4-nano",
    )


def test_build_litellm_agent_client_for_azure_openai() -> None:
    client = build_litellm_agent_client(_azure_settings(), "azure-openai")

    assert isinstance(client, LiteLLMAgentClient)
    assert client._litellm_model == "azure/gpt-5.4-mini"
    assert client._api_base == "https://example.openai.azure.com"
    assert client._api_version == "2024-10-21"
    assert client._api_key_env == "AZURE_OPENAI_API_KEY"


def test_build_litellm_llm_client_for_azure_openai() -> None:
    client = build_litellm_llm_client(
        _azure_settings(),
        "azure-openai",
        "reasoning",
    )

    assert isinstance(client, LiteLLMLLMClient)
    assert client._litellm_model == "azure/gpt-5.4-mini"
    assert client._api_base == "https://example.openai.azure.com"
    assert client._api_version == "2024-10-21"
    assert client._model_fallback == "azure/gpt-5.4-nano"


def test_azure_openai_requires_base_url_in_settings() -> None:
    settings = _azure_settings()
    settings.azure_openai_base_url = ""
    with pytest.raises(RuntimeError, match="AZURE_OPENAI_BASE_URL"):
        build_litellm_agent_client(settings, "azure-openai")
