"""Provider-awareness tests for chat node LLM initialization."""

from __future__ import annotations

import pytest

from app.nodes import chat
from app.services.chat_sdk_adapter import _AnthropicChatAdapter, _OpenAIChatAdapter


def _reset_chat_cache(monkeypatch) -> None:
    """
    Reset cached chat model singletons between test runs.

    Args:
        monkeypatch: The monkeypatch object.

    Returns:
        None.
    """
    monkeypatch.setattr(chat, "_chat_llm_cache", {})
    monkeypatch.setattr(chat, "_chat_llm_with_tools_cache", {})


def test_get_chat_llm_uses_openai_toolcall_model_when_provider_openai(
    monkeypatch,
) -> None:
    """
    Build OpenAI tool-bound model when provider is openai.

    Args:
        monkeypatch: The monkeypatch object.

    Returns:
        None.
    """
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_TOOLCALL_MODEL", "gpt-openai-tools")
    monkeypatch.setenv("OPENAI_REASONING_MODEL", "gpt-openai-reasoning")
    monkeypatch.delenv("ANTHROPIC_TOOLCALL_MODEL", raising=False)
    monkeypatch.delenv("ANTHROPIC_REASONING_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    _reset_chat_cache(monkeypatch)

    llm = chat._get_chat_llm(with_tools=True)
    assert isinstance(llm, _OpenAIChatAdapter)
    assert llm._model == "gpt-openai-tools"
    assert llm._with_tools is True


def test_get_chat_llm_uses_openai_reasoning_model_when_without_tools(
    monkeypatch,
) -> None:
    """
    Build OpenAI reasoning model when tools are disabled.

    Args:
        monkeypatch: The monkeypatch object.

    Returns:
        None.
    """
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_REASONING_MODEL", "gpt-openai-reasoning")
    monkeypatch.delenv("OPENAI_TOOLCALL_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    _reset_chat_cache(monkeypatch)

    llm = chat._get_chat_llm(with_tools=False)
    assert isinstance(llm, _OpenAIChatAdapter)
    assert llm._model == "gpt-openai-reasoning"
    assert llm._with_tools is False


def test_get_chat_llm_uses_anthropic_models(monkeypatch) -> None:
    """
    Build Anthropic tool and reasoning models from env values.

    Args:
        monkeypatch: The monkeypatch object.

    Returns:
        None.
    """
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_TOOLCALL_MODEL", "claude-tools")
    monkeypatch.setenv("ANTHROPIC_REASONING_MODEL", "claude-reason")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    _reset_chat_cache(monkeypatch)

    tool_llm = chat._get_chat_llm(with_tools=True)
    reasoning_llm = chat._get_chat_llm(with_tools=False)

    assert isinstance(tool_llm, _AnthropicChatAdapter)
    assert tool_llm._model == "claude-tools"
    assert tool_llm._with_tools is True

    assert isinstance(reasoning_llm, _AnthropicChatAdapter)
    assert reasoning_llm._model == "claude-reason"
    assert reasoning_llm._with_tools is False


def test_get_chat_llm_rebuilds_when_provider_changes(monkeypatch) -> None:
    """
    Rebuild cached tool model when provider changes across calls.

    Args:
        monkeypatch: The monkeypatch object.

    Returns:
        None.
    """
    monkeypatch.setenv("OPENAI_TOOLCALL_MODEL", "gpt-openai-tools")
    monkeypatch.setenv("ANTHROPIC_TOOLCALL_MODEL", "claude-tools")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    _reset_chat_cache(monkeypatch)

    monkeypatch.setenv("LLM_PROVIDER", "openai")
    llm_openai = chat._get_chat_llm(with_tools=True)

    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    llm_anthropic = chat._get_chat_llm(with_tools=True)

    assert isinstance(llm_openai, _OpenAIChatAdapter)
    assert isinstance(llm_anthropic, _AnthropicChatAdapter)


def test_get_chat_llm_raises_for_unsupported_provider(monkeypatch) -> None:
    """
    Raise ValueError when provider value is unsupported.

    Args:
        monkeypatch: The monkeypatch object.

    Returns:
        None.
    """
    monkeypatch.setenv("LLM_PROVIDER", "unsupported")
    _reset_chat_cache(monkeypatch)

    with pytest.raises(ValueError):
        chat._get_chat_llm(with_tools=True)
