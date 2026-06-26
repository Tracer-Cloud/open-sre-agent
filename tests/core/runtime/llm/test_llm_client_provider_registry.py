"""Dispatch tests for the OpenAI-compatible provider registry in ``llm_client``.

Guards the refactor that collapsed six near-identical ``elif provider == ...``
branches in ``_create_llm_client`` into ``_OPENAI_COMPATIBLE_PROVIDERS``. The
registry is pure data, so these assert both the data and that ``_create_llm_client``
wires each provider into ``OpenAILLMClient`` with the right base URL, API-key env
var, temperature, and model.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import core.runtime.llm.llm_client as llm_client

_OPENAI_COMPATIBLE_PROVIDERS = llm_client._OPENAI_COMPATIBLE_PROVIDERS
OpenAILLMClient = llm_client.OpenAILLMClient
_create_llm_client = llm_client._create_llm_client


def test_registry_entries_are_well_formed() -> None:
    assert set(_OPENAI_COMPATIBLE_PROVIDERS) == {
        "openrouter",
        "deepseek",
        "gemini",
        "nvidia",
        "minimax",
        "groq",
    }
    for name, spec in _OPENAI_COMPATIBLE_PROVIDERS.items():
        assert spec.base_url.startswith("http"), name
        assert spec.api_key_env.endswith("_API_KEY"), name
    # MiniMax is the only registry provider that pins a non-default temperature.
    assert _OPENAI_COMPATIBLE_PROVIDERS["minimax"].temperature == 1.0
    assert _OPENAI_COMPATIBLE_PROVIDERS["openrouter"].temperature is None


@pytest.mark.parametrize("provider", sorted(_OPENAI_COMPATIBLE_PROVIDERS))
def test_create_llm_client_dispatches_registry_provider(
    provider: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = _OPENAI_COMPATIBLE_PROVIDERS[provider]
    # model_type="toolcall" makes _fallback_model() short-circuit to None, so the
    # stub only needs the provider name and the toolcall model attribute.
    settings = SimpleNamespace(provider=provider, **{f"{provider}_toolcall_model": "stub-model"})
    monkeypatch.setattr(llm_client, "resolve_llm_settings", lambda: settings)

    client = _create_llm_client("toolcall")

    # OpenAILLMClient exposes no public getters for these construction params, so
    # we deliberately read the private attributes to pin that the registry wired
    # each provider correctly. A rename surfaces loudly as an AttributeError here.
    assert isinstance(client, OpenAILLMClient)
    assert client._base_url == spec.base_url
    assert client._api_key_env == spec.api_key_env
    assert client._model == "stub-model"
    assert client._temperature == spec.temperature
    assert client._max_tokens == spec.config.max_tokens
