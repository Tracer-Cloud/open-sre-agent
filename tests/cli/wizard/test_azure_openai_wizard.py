"""Tests for Azure OpenAI wizard onboarding helpers."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from surfaces.cli.wizard import _ui, azure_openai


def test_choose_azure_deployment_lists_resource_deployments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        azure_openai,
        "discover_azure_openai_deployments_from_env",
        lambda: ["gpt-4.1", "my-custom-deployment"],
    )

    captured: dict[str, list[str]] = {}

    def _mock_select(_prompt: str, choices: list[Any], **_kwargs: Any) -> Any:
        captured["values"] = [choice.value for choice in choices]
        m = MagicMock()
        m.ask.return_value = "gpt-4.1"
        return m

    monkeypatch.setattr(_ui, "select_prompt", _mock_select)

    deployment = azure_openai.choose_azure_deployment(default="")

    assert deployment == "gpt-4.1"
    assert captured["values"][:2] == ["gpt-4.1", "my-custom-deployment"]
    assert captured["values"][-1] == _ui._CUSTOM_MODEL_SENTINEL


def test_choose_azure_deployment_prompts_manual_entry_when_discovery_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(azure_openai, "discover_azure_openai_deployments_from_env", lambda: [])
    monkeypatch.setattr(
        azure_openai,
        "_prompt_value",
        lambda *_args, **_kwargs: "manual-deployment",
    )

    deployment = azure_openai.choose_azure_deployment(default="")

    assert deployment == "manual-deployment"


def test_format_validation_failure_lists_deployments(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        azure_openai,
        "list_azure_openai_deployments",
        lambda **_kwargs: ["gpt-4.1-mini"],
    )

    detail = azure_openai.format_validation_failure(
        deployment="gpt-4.1",
        base_url="https://example.openai.azure.com",
        api_key="test-key",
        api_version="2024-10-21",
        error=RuntimeError("Error code: 404 - DeploymentNotFound"),
    )

    assert "deployment 'gpt-4.1'" in detail
    assert "Available deployments: gpt-4.1-mini" in detail


def test_format_validation_failure_passthrough_for_other_errors() -> None:
    detail = azure_openai.format_validation_failure(
        deployment="gpt-4.1",
        base_url="https://example.openai.azure.com",
        api_key="test-key",
        api_version="2024-10-21",
        error=RuntimeError("connection reset"),
    )
    assert detail == "Validation request failed: connection reset"
