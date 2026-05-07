"""Tests for chat branch LLM provider selection (LLM_PROVIDER)."""

from __future__ import annotations

import pytest

from app.nodes import chat as chat_mod
from app.services.chat_sdk_adapter import _AnthropicChatAdapter, _OpenAIChatAdapter


def _clear_chat_llm_singletons() -> None:
    """Reset module-level chat model cache (isolated test runs)."""
    chat_mod._chat_llm_cache.clear()
    chat_mod._chat_llm_with_tools_cache.clear()


@pytest.fixture
def openai_chat_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")


@pytest.mark.usefixtures("openai_chat_env")
def test_get_chat_llm_openai_with_tools_returns_openai_adapter() -> None:
    _clear_chat_llm_singletons()
    out = chat_mod._get_chat_llm(with_tools=True)
    assert isinstance(out, _OpenAIChatAdapter)
    assert out._with_tools is True


@pytest.mark.usefixtures("openai_chat_env")
def test_get_chat_llm_openai_without_tools_returns_openai_adapter() -> None:
    _clear_chat_llm_singletons()
    out = chat_mod._get_chat_llm(with_tools=False)
    assert isinstance(out, _OpenAIChatAdapter)
    assert out._with_tools is False


def test_get_chat_llm_anthropic_returns_anthropic_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    _clear_chat_llm_singletons()
    out = chat_mod._get_chat_llm(with_tools=False)
    assert isinstance(out, _AnthropicChatAdapter)


def test_general_node_returns_user_facing_message_for_codex_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "codex")
    chat_mod._chat_llm_cache.clear()
    state = {"messages": [{"role": "user", "content": "hello"}]}

    out = chat_mod.general_node(state, {"configurable": {}})

    assert out["messages"]
    assert (
        "Interactive chat requires LLM_PROVIDER=anthropic or openai."
        in out["messages"][0]["content"]
    )
