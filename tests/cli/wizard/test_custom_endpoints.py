"""Onboarding endpoint collection for the custom OpenAI-/Anthropic-compatible gateways."""

from __future__ import annotations

from typing import Any

import pytest

from surfaces.cli.wizard.config import PROVIDER_BY_VALUE
from surfaces.cli.wizard.custom_endpoints import ensure_endpoint_settings


def test_configured_custom_openai_short_circuits_and_keeps_v1(monkeypatch: Any) -> None:
    # custom-openai keeps its /v1 verbatim (the OpenAI client appends /chat/completions).
    monkeypatch.setenv("CUSTOM_OPENAI_BASE_URL", "http://localhost:4000/v1")
    result = ensure_endpoint_settings(PROVIDER_BY_VALUE["custom-openai"])
    assert result == {"CUSTOM_OPENAI_BASE_URL": "http://localhost:4000/v1"}


def test_configured_custom_anthropic_strips_trailing_v1(monkeypatch: Any) -> None:
    # custom-anthropic strips a trailing /v1 (the Anthropic SDK appends /v1/messages).
    monkeypatch.setenv("CUSTOM_ANTHROPIC_BASE_URL", "https://proxy.example.com/v1")
    result = ensure_endpoint_settings(PROVIDER_BY_VALUE["custom-anthropic"])
    assert result == {"CUSTOM_ANTHROPIC_BASE_URL": "https://proxy.example.com"}


def test_unset_endpoint_prompts(monkeypatch: Any) -> None:
    # When the endpoint env is unset the module prompts; stub the prompt to a value.
    monkeypatch.delenv("CUSTOM_OPENAI_BASE_URL", raising=False)
    import surfaces.cli.wizard._ui as ui

    monkeypatch.setattr(ui, "_prompt_value", lambda *_a, **_k: "http://gw.internal:8000/v1")
    result = ensure_endpoint_settings(PROVIDER_BY_VALUE["custom-openai"])
    assert result == {"CUSTOM_OPENAI_BASE_URL": "http://gw.internal:8000/v1"}


@pytest.mark.parametrize("slug", ["custom-openai", "custom-anthropic"])
def test_blank_prompt_is_rejected(monkeypatch: Any, slug: str) -> None:
    endpoint_env = PROVIDER_BY_VALUE[slug].endpoint_env
    monkeypatch.delenv(endpoint_env, raising=False)
    import surfaces.cli.wizard._ui as ui

    monkeypatch.setattr(ui, "_prompt_value", lambda *_a, **_k: "")
    assert ensure_endpoint_settings(PROVIDER_BY_VALUE[slug]) is None
