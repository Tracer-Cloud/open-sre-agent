from __future__ import annotations

from config.config import get_configured_llm_provider

UNSUPPORTED_SYNTHETIC_LLM_PROVIDERS = frozenset({"ollama"})


class UnsupportedSyntheticLLMProviderError(RuntimeError):
    """Raised when a synthetic suite is run with a known-unsupported LLM provider."""


def validate_synthetic_llm_provider(
    *,
    suite_name: str,
    offline_hint: str | None = None,
) -> None:
    """Fail fast for LLM providers that cannot reliably run scored synthetic suites."""
    provider = get_configured_llm_provider()
    if provider not in UNSUPPORTED_SYNTHETIC_LLM_PROVIDERS:
        return

    hint = (
        f" Use {offline_hint} for fixture-only checks."
        if offline_hint
        else " Use a hosted provider such as LLM_PROVIDER=openai or LLM_PROVIDER=anthropic."
    )
    raise UnsupportedSyntheticLLMProviderError(
        f"{suite_name} synthetic tests are not supported with LLM_PROVIDER={provider}. "
        "Local Ollama models currently produce unreliable synthetic-test failures rather than "
        f"stable benchmark signal.{hint}"
    )
