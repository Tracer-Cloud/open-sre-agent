"""Adapters that bind current-main Anthropic clients to a custom endpoint.

The base SDK clients retain prompt caching, cache-marker fallback, streaming,
structured-output, and usage behavior from current main. This module only
supplies the custom endpoint and credential source required by issue #2682.
"""

from __future__ import annotations

from typing import Any


def build_custom_anthropic_agent_client(
    *, model: str, max_tokens: int, base_url: str, api_key_env: str
) -> Any:
    """Build the current-main agent client against a custom Anthropic endpoint."""
    from anthropic import Anthropic

    from config.llm_credentials import resolve_env_credential
    from core.llm.shared.openai_chat_completions import AGENT_CLIENT_TIMEOUT_SEC
    from core.llm.transports.sdk.agent_clients import AnthropicAgentClient

    api_key = resolve_env_credential(api_key_env)
    client = Anthropic(
        api_key=api_key,
        base_url=base_url,
        timeout=AGENT_CLIENT_TIMEOUT_SEC,
    )
    result = AnthropicAgentClient(model=model, max_tokens=max_tokens, client=client)
    result.auth_error_hint = f"Check {api_key_env}."
    return result


class CustomAnthropicLLMClient:
    """Factory-compatible proxy around the current-main Anthropic LLM client."""

    def __new__(
        cls,
        *,
        model: str,
        max_tokens: int,
        base_url: str,
        api_key_env: str,
        temperature: float | None = None,
    ) -> Any:
        from anthropic import Anthropic

        from config.llm_auth import credentials as provider_credentials
        from core.llm.transports.sdk.llm_clients import LLMClient, LLM_CLIENT_TIMEOUT_SEC

        class _BoundCustomAnthropicClient(LLMClient):
            def __init__(self) -> None:
                self._api_key_env = api_key_env
                self._base_url = base_url
                api_key = provider_credentials.resolve_llm_api_key(api_key_env)
                self._api_key = api_key
                self._client = self._new_client(api_key)
                self._model = model
                self._max_tokens = max_tokens
                self._temperature = temperature

            def _new_client(self, api_key: str) -> Anthropic:
                return Anthropic(
                    api_key=api_key,
                    base_url=self._base_url,
                    timeout=LLM_CLIENT_TIMEOUT_SEC,
                )

            def _ensure_client(self) -> None:
                api_key = provider_credentials.resolve_llm_api_key(self._api_key_env)
                if not api_key:
                    raise RuntimeError(
                        f"Missing {self._api_key_env}. Set it in your environment, .env, "
                        "or secure local keychain before running LLM steps."
                    )
                if api_key != self._api_key:
                    self._api_key = api_key
                    self._client = self._new_client(api_key)

        return _BoundCustomAnthropicClient()
