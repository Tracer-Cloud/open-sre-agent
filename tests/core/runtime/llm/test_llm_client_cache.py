"""LLM singleton cache invalidation tests."""

from __future__ import annotations

from core.llm import agent_llm_client, llm_client


def test_llm_singleton_invalidates_on_provider_change(monkeypatch) -> None:
    created: list[object] = []

    def fake_create(*, model_type: str) -> object:
        marker = object()
        created.append(marker)
        return marker

    monkeypatch.setattr(llm_client, "_create_llm_client", fake_create)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    llm_client.reset_llm_singletons()

    first = llm_client.get_llm_for_reasoning()
    monkeypatch.setenv("LLM_PROVIDER", "azure-openai")
    monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://example.openai.azure.com")
    second = llm_client.get_llm_for_reasoning()

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
        "core.llm.litellm.routing.build_litellm_agent_client",
        fake_build,
    )
    monkeypatch.setenv("LLM_PROVIDER", "azure-openai")
    monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://example.openai.azure.com")
    agent_llm_client.reset_agent_client()

    first = agent_llm_client.get_agent_llm()
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_REASONING_MODEL", "deepseek-v4-pro")
    monkeypatch.setenv("OPENSRE_LLM_TRANSPORT", "litellm")
    second = agent_llm_client.get_agent_llm()

    assert first is not second
    assert len(created) == 2
