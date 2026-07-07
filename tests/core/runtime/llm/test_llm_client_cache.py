"""LLM singleton cache invalidation tests."""

from __future__ import annotations

from core.llm.factory import LLMRole, get_llm, reset_llm_clients


def test_llm_singleton_invalidates_on_provider_change(monkeypatch) -> None:
    created: list[object] = []

    def fake_build(_route: object, _model_type: str) -> object:
        marker = object()
        created.append(marker)
        return marker

    monkeypatch.setattr("core.llm.client_builders.build_reasoning_client", fake_build)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    reset_llm_clients()

    first = get_llm(LLMRole.REASONING)
    monkeypatch.setenv("LLM_PROVIDER", "azure-openai")
    monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://example.openai.azure.com")
    second = get_llm(LLMRole.REASONING)

    assert first is not second
    assert len(created) == 2


def test_agent_singleton_invalidates_on_provider_change(monkeypatch) -> None:
    created: list[object] = []

    class _StubAgentClient:
        pass

    def fake_build(_settings: object, provider: str) -> _StubAgentClient:
        client = _StubAgentClient()
        created.append(client)
        return client

    monkeypatch.setattr(
        "core.llm.transports.litellm.routing.build_litellm_agent_client",
        fake_build,
    )
    monkeypatch.setenv("LLM_PROVIDER", "azure-openai")
    monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://example.openai.azure.com")
    reset_llm_clients()

    first = get_llm(LLMRole.AGENT)
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_REASONING_MODEL", "deepseek-v4-pro")
    monkeypatch.setenv("OPENSRE_LLM_TRANSPORT", "litellm")
    second = get_llm(LLMRole.AGENT)

    assert first is not second
    assert len(created) == 2
