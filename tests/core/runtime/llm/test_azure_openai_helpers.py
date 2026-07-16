"""Azure OpenAI helper tests."""

from __future__ import annotations

import pytest

from core.llm.provider_errors import LLMResourceNotFoundError
from core.llm.providers.azure_openai import azure_openai_deployment_not_found_detail
from core.llm.shared.openai_chat_completions import invoke_with_litellm_agent_retries


class NotFoundError(Exception):
    pass


def test_azure_openai_deployment_not_found_detail_explains_model_id_mismatch() -> None:
    detail = azure_openai_deployment_not_found_detail(deployment="gpt-4.1")

    assert "deployment 'gpt-4.1' was not found" in detail
    assert "not a model ID" in detail
    assert "Azure Resource Manager API" in detail


def test_invoke_with_litellm_agent_retries_raises_structured_resource_error() -> None:
    def completion(**_kwargs: object) -> None:
        raise NotFoundError("deployment not found")

    with pytest.raises(LLMResourceNotFoundError) as exc_info:
        invoke_with_litellm_agent_retries(
            completion,
            {
                "model": "azure/gpt-4.1",
                "messages": [{"role": "user", "content": "hi"}],
                "api_base": "https://example.openai.azure.com",
                "api_version": "2024-10-21",
                "api_key": "test-key",
            },
            provider_name="Azure OpenAI",
            provider_id="azure-openai",
            model="azure/gpt-4.1",
            resource_kind="deployment",
            resource_name="gpt-4.1",
            resource_not_found_detail=azure_openai_deployment_not_found_detail(
                deployment="gpt-4.1"
            ),
        )

    assert exc_info.value.provider == "azure-openai"
    assert exc_info.value.provider_label == "Azure OpenAI"
    assert exc_info.value.resource_kind == "deployment"
    assert exc_info.value.resource_name == "gpt-4.1"
    assert "not a model ID returned by GET /openai/models" in str(exc_info.value)
