from __future__ import annotations

import pytest

from tests.synthetic.llm_provider_preflight import (
    UnsupportedSyntheticLLMProviderError,
    validate_synthetic_llm_provider,
)


def test_preflight_allows_hosted_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")

    validate_synthetic_llm_provider(suite_name="RDS PostgreSQL")


def test_preflight_rejects_ollama_with_actionable_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "ollama")

    with pytest.raises(UnsupportedSyntheticLLMProviderError) as exc_info:
        validate_synthetic_llm_provider(
            suite_name="Hermes RCA",
            offline_hint="--offline-only",
        )

    message = str(exc_info.value)
    assert "Hermes RCA synthetic tests are not supported with LLM_PROVIDER=ollama" in message
    assert "Local Ollama models" in message
    assert "--offline-only" in message
