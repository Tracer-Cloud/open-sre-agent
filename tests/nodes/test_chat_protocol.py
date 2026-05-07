"""Contract tests for direct-SDK chat adapters and neutral message types (issue #1363)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.nodes import chat as chat_mod
from app.services.chat_sdk_adapter import (
    _AnthropicChatAdapter,
    _OpenAIChatAdapter,
    messages_to_invocation_dicts,
)

# ── OpenAI adapter ────────────────────────────────────────────────────────────


def _openai_response(content: str = "", tool_calls: list | None = None) -> Any:
    """Build a minimal fake openai chat completion response."""
    tc_objs = []
    for tc in tool_calls or []:
        fn = SimpleNamespace(name=tc["name"], arguments=json.dumps(tc.get("args", {})))
        tc_objs.append(SimpleNamespace(id=tc["id"], type="function", function=fn))
    message = SimpleNamespace(content=content, tool_calls=tc_objs or None)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


def test_openai_adapter_returns_plain_text_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    adapter = _OpenAIChatAdapter(model="gpt-4o", with_tools=False)
    fake_response = _openai_response(content="hello there")

    with patch("app.services.chat_sdk_adapter.OpenAI") as mock_cls:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = fake_response
        mock_cls.return_value = mock_client

        out = adapter.invoke([{"role": "user", "content": "hi"}])

    assert out == {"content": "hello there"}


def test_openai_adapter_maps_tool_calls_to_neutral_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    adapter = _OpenAIChatAdapter(model="gpt-4o", with_tools=True)
    fake_response = _openai_response(
        content="",
        tool_calls=[{"id": "call_1", "name": "my_tool", "args": {"x": 1}}],
    )

    with patch("app.services.chat_sdk_adapter.OpenAI") as mock_cls:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = fake_response
        mock_cls.return_value = mock_client

        out = adapter.invoke([{"role": "user", "content": "run"}])

    assert out["content"] == ""
    assert out["tool_calls"] == [{"id": "call_1", "name": "my_tool", "args": {"x": 1}}]


# ── Anthropic adapter ─────────────────────────────────────────────────────────


def _anthropic_response(text: str = "", tool_uses: list | None = None) -> Any:
    """Build a minimal fake anthropic messages response."""
    blocks: list[Any] = []
    if text:
        blocks.append(SimpleNamespace(type="text", text=text))
    for tu in tool_uses or []:
        blocks.append(
            SimpleNamespace(
                type="tool_use",
                id=tu["id"],
                name=tu["name"],
                input=tu.get("args", {}),
            )
        )
    return SimpleNamespace(content=blocks)


def test_anthropic_adapter_returns_plain_text_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    adapter = _AnthropicChatAdapter(model="claude-3-5-sonnet-20241022", with_tools=False)
    fake_response = _anthropic_response(text="hello there")

    with patch("app.services.chat_sdk_adapter.Anthropic") as mock_cls:
        mock_client = MagicMock()
        mock_client.messages.create.return_value = fake_response
        mock_cls.return_value = mock_client

        out = adapter.invoke([{"role": "user", "content": "hi"}])

    assert out == {"content": "hello there"}


def test_anthropic_adapter_maps_tool_use_blocks_to_neutral_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    adapter = _AnthropicChatAdapter(model="claude-3-5-sonnet-20241022", with_tools=True)
    fake_response = _anthropic_response(
        tool_uses=[{"id": "tu_1", "name": "my_tool", "args": {"y": 2}}]
    )

    with patch("app.services.chat_sdk_adapter.Anthropic") as mock_cls:
        mock_client = MagicMock()
        mock_client.messages.create.return_value = fake_response
        mock_cls.return_value = mock_client

        out = adapter.invoke([{"role": "user", "content": "run"}])

    assert out["content"] == ""
    assert out["tool_calls"] == [{"id": "tu_1", "name": "my_tool", "args": {"y": 2}}]


def test_anthropic_adapter_splits_system_into_top_level_param(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    adapter = _AnthropicChatAdapter(model="claude-3-5-sonnet-20241022", with_tools=False)
    fake_response = _anthropic_response(text="ok")

    with patch("app.services.chat_sdk_adapter.Anthropic") as mock_cls:
        mock_client = MagicMock()
        mock_client.messages.create.return_value = fake_response
        mock_cls.return_value = mock_client

        adapter.invoke(
            [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "hello"},
            ]
        )

    _, kwargs = mock_client.messages.create.call_args
    assert kwargs.get("system") == "You are helpful."
    for msg in kwargs.get("messages", []):
        assert msg.get("role") != "system"


# ── messages_to_invocation_dicts ──────────────────────────────────────────────


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


# ── Codex / unsupported provider path ────────────────────────────────────────


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
