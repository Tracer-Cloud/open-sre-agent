"""Azure OpenAI provider helper tests."""

from __future__ import annotations

from core.llm.providers.azure_openai import (
    azure_deployments_list_curl_command,
    azure_openai_deployment_name,
    azure_openai_litellm_model,
    discover_azure_openai_deployments_from_env,
    format_azure_deployment_not_found_message,
    is_azure_deployment_lookup_error,
    is_azure_litellm_model,
    is_azure_openai_failure_message,
    list_azure_openai_deployments,
)


def test_azure_openai_litellm_model_adds_prefix() -> None:
    assert azure_openai_litellm_model("gpt-4.1") == "azure/gpt-4.1"
    assert azure_openai_litellm_model("azure/gpt-4.1") == "azure/gpt-4.1"


def test_azure_openai_deployment_name_strips_prefix() -> None:
    assert azure_openai_deployment_name("azure/gpt-4.1") == "gpt-4.1"
    assert azure_openai_deployment_name("gpt-4.1") == "gpt-4.1"


def test_is_azure_litellm_model() -> None:
    assert is_azure_litellm_model("azure/gpt-4.1") is True
    assert is_azure_litellm_model("gpt-4.1") is False


def test_is_azure_deployment_lookup_error() -> None:
    assert is_azure_deployment_lookup_error(RuntimeError("Error code: 404")) is True
    assert is_azure_deployment_lookup_error(RuntimeError("connection reset")) is False


def test_is_azure_openai_failure_message() -> None:
    assert (
        is_azure_openai_failure_message("Azure OpenAI deployment 'gpt-4.1' was not found.") is True
    )
    assert is_azure_openai_failure_message("OpenAI model 'gpt-4' was not found.") is False


def test_azure_deployments_list_curl_command_uses_default_api_version() -> None:
    command = azure_deployments_list_curl_command()
    assert "api-version=2024-10-21" in command
    assert "$AZURE_OPENAI_BASE_URL/openai/deployments" in command


def test_format_azure_deployment_not_found_message_mentions_deployment() -> None:
    message = format_azure_deployment_not_found_message("azure/gpt-4.1")
    assert "deployment 'gpt-4.1'" in message
    assert "/openai/models" in message
    assert "/openai/deployments" in message


def test_list_azure_openai_deployments_parses_response(monkeypatch) -> None:
    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"data": [{"id": "gpt-4.1"}, {"id": "my-custom-name"}]}

    monkeypatch.setattr(
        "httpx.get",
        lambda *_args, **_kwargs: _FakeResponse(),
    )

    deployments = list_azure_openai_deployments(
        base_url="https://example.openai.azure.com",
        api_key="test-key",
    )
    assert deployments == ["gpt-4.1", "my-custom-name"]


def test_discover_azure_openai_deployments_from_env(monkeypatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://example.openai.azure.com")
    monkeypatch.setattr(
        "config.llm_credentials.resolve_llm_api_key",
        lambda _env: "test-key",
    )
    monkeypatch.setattr(
        "core.llm.providers.azure_openai.list_azure_openai_deployments",
        lambda **_kwargs: ["gpt-4.1-mini"],
    )

    assert discover_azure_openai_deployments_from_env() == ["gpt-4.1-mini"]
