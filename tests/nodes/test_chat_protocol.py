"""Contract tests for neutral chat LLM types and LangChain adapter (issue #1358)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.nodes import chat as chat_mod
from app.services.chat_langchain_adapter import (
    _LangChainBoundChatWrapper,
    messages_to_invocation_dicts,
)


def test_wrapper_returns_assistant_turn_dict() -> None:
    inner = MagicMock()
    inner.invoke.return_value = AIMessage(content="hello")
    wrapper = _LangChainBoundChatWrapper(inner)
    out = wrapper.invoke([HumanMessage(content="hi")])
    assert out == {"content": "hello"}


def test_wrapper_maps_tool_calls_to_neutral_payloads() -> None:
    inner = MagicMock()
    inner.invoke.return_value = AIMessage(
        content="",
        tool_calls=[{"id": "call_1", "name": "my_tool", "args": {"x": 1}}],
    )
    wrapper = _LangChainBoundChatWrapper(inner)
    out = wrapper.invoke([{"role": "user", "content": "run"}])
    assert out["content"] == ""
    assert out["tool_calls"] == [
        {"id": "call_1", "name": "my_tool", "args": {"x": 1}},
    ]


def test_messages_to_invocation_dicts_handles_lc_base_messages() -> None:
    msgs: list[object] = [
        HumanMessage(content="hi"),
        AIMessage(content="yo", tool_calls=[{"id": "a", "name": "t", "args": {}}]),
    ]
    d = messages_to_invocation_dicts(msgs)
    assert d[0] == {"role": "user", "content": "hi"}
    assert d[1]["role"] == "assistant"
    assert d[1]["content"] == "yo"
    assert d[1]["tool_calls"]


def test_codex_general_node_error_is_plain_dict_assistant_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unsupported provider path must persist dict messages (not LC AIMessage)."""
    monkeypatch.setenv("LLM_PROVIDER", "codex")
    chat_mod._chat_llm_cache.clear()
    out = chat_mod.general_node(
        {"messages": [{"role": "user", "content": "hello"}]}, {"configurable": {}}
    )
    msg = out["messages"][0]
    assert isinstance(msg, dict)
    assert msg.get("role") == "assistant"
    assert isinstance(msg.get("content"), str)
