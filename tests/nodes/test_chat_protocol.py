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
    _normalize_messages_for_anthropic,
    _normalize_messages_for_openai,
    _OpenAIChatAdapter,
    _split_system_messages,
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

    with patch("openai.OpenAI") as mock_cls:
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

    with patch("openai.OpenAI") as mock_cls:
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

    with patch("anthropic.Anthropic") as mock_cls:
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

    with patch("anthropic.Anthropic") as mock_cls:
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

    with patch("anthropic.Anthropic") as mock_cls:
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


def test_anthropic_adapter_raises_on_empty_messages_after_system_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anthropic rejects messages=[]; the adapter must raise before hitting the API."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    adapter = _AnthropicChatAdapter(model="claude-3-5-sonnet-20241022", with_tools=False)

    # Case 1: fully empty input
    with pytest.raises(ValueError, match="empty messages list"):
        adapter.invoke([])

    # Case 2: only system messages — all consumed by _split_system_messages
    with pytest.raises(ValueError, match="empty messages list"):
        adapter.invoke([{"role": "system", "content": "You are helpful."}])

    # Case 3: multiple system-only messages
    with pytest.raises(ValueError, match="empty messages list"):
        adapter.invoke(
            [
                {"role": "system", "content": "First system."},
                {"role": "system", "content": "Second system."},
            ]
        )


# ── messages_to_invocation_dicts ──────────────────────────────────────────────


def test_openai_normalize_forwards_tool_message_name() -> None:
    out = _normalize_messages_for_openai(
        [
            {
                "role": "tool",
                "content": "result",
                "tool_call_id": "call-1",
                "name": "my_tool",
            }
        ]
    )
    assert out[0]["role"] == "tool"
    assert out[0]["name"] == "my_tool"


def test_split_system_serializes_non_string_content() -> None:
    system, rest = _split_system_messages(
        [
            {"role": "system", "content": {"directive": "be brief"}},
            {"role": "user", "content": "hello"},
        ]
    )
    assert system is not None
    assert "directive" in system
    assert len(rest) == 1


def test_split_system_only_leading_contiguous() -> None:
    system, rest = _split_system_messages(
        [
            {"role": "system", "content": "a"},
            {"role": "user", "content": "hi"},
            {"role": "system", "content": "b"},
        ]
    )
    assert system == "a"
    assert rest == [
        {"role": "user", "content": "hi"},
        {"role": "system", "content": "b"},
    ]


def test_normalize_anthropic_merges_consecutive_tool_results() -> None:
    # Two back-to-back tool messages become one role:user turn with tool_result blocks.
    # The following role:user message is merged into the same turn because Anthropic
    # rejects consecutive user turns — so the final output is a single user turn.
    out = _normalize_messages_for_anthropic(
        [
            {"role": "tool", "content": "r1", "tool_call_id": "id1"},
            {"role": "tool", "content": "r2", "tool_call_id": "id2"},
            {"role": "user", "content": "next"},
        ]
    )
    assert len(out) == 1
    assert out[0]["role"] == "user"
    blocks = out[0]["content"]
    assert isinstance(blocks, list) and len(blocks) == 3
    assert blocks[0]["type"] == "tool_result" and blocks[0]["tool_use_id"] == "id1"
    assert blocks[1]["type"] == "tool_result" and blocks[1]["tool_use_id"] == "id2"
    assert blocks[2] == {"type": "text", "text": "next"}


def test_normalize_anthropic_non_leading_system_merges_into_prior_user() -> None:
    out = _normalize_messages_for_anthropic(
        [{"role": "user", "content": "hi"}, {"role": "system", "content": "injected"}]
    )
    assert len(out) == 1
    assert out[0] == {
        "role": "user",
        "content": "hi\n\n[system]\ninjected",
    }


def test_normalize_anthropic_system_after_assistant_is_own_user_turn() -> None:
    # [assistant, system] — system becomes a standalone user turn (no following user to merge into)
    out = _normalize_messages_for_anthropic(
        [
            {"role": "assistant", "content": "done"},
            {"role": "system", "content": "extra"},
        ]
    )
    assert len(out) == 2
    assert out[0] == {"role": "assistant", "content": "done"}
    assert out[1] == {
        "role": "user",
        "content": [{"type": "text", "text": "extra"}],
    }


def test_normalize_anthropic_assistant_system_user_no_consecutive_user_turns() -> None:
    # Greptile-reported defect: [assistant, system, user] produced two consecutive user turns
    # which the Anthropic API rejects with a 400. The system turn must be merged into the user.
    out = _normalize_messages_for_anthropic(
        [
            {"role": "assistant", "content": "Let me check."},
            {"role": "system", "content": "Injected context."},
            {"role": "user", "content": "What next?"},
        ]
    )
    roles = [m["role"] for m in out]
    assert roles == ["assistant", "user"], f"Consecutive user turns produced: {roles}"
    # The merged user turn must contain both the system and user text
    merged = out[1]["content"]
    if isinstance(merged, str):
        assert "Injected context." in merged
        assert "What next?" in merged
    else:
        texts = " ".join(b.get("text", "") for b in merged if isinstance(b, dict))
        assert "Injected context." in texts
        assert "What next?" in texts


def test_normalize_anthropic_system_merges_into_user_with_tool_result_blocks() -> None:
    out = _normalize_messages_for_anthropic(
        [
            {"role": "tool", "content": "r1", "tool_call_id": "id1"},
            {"role": "system", "content": "follow-up instruction"},
        ]
    )
    assert len(out) == 1
    blocks = out[0]["content"]
    assert isinstance(blocks, list)
    assert blocks[0]["type"] == "tool_result"
    assert blocks[1]["type"] == "text"
    assert "[system]" in str(blocks[1].get("text", ""))


def test_chat_openai_tool_result_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    chat_mod.reset_chat_llm_cache()

    def stub_tool(**_kw: Any) -> str:
        return "from-tool"

    stub_tool.name = "stub_tool"
    stub_tool.description = "Round-trip test stub."
    stub_tool.input_schema = {"type": "object", "properties": {}}

    first = _openai_response(
        content="",
        tool_calls=[{"id": "tc1", "name": "stub_tool", "args": {}}],
    )
    second = _openai_response(content="Done.")

    fake_tools = [stub_tool]
    with (
        patch("openai.OpenAI") as mock_openai,
        patch("app.nodes.chat.get_registered_tools", return_value=fake_tools),
        patch("app.services.chat_sdk_adapter.get_registered_tools", return_value=fake_tools),
    ):
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = [first, second]
        mock_openai.return_value = mock_client

        state: dict[str, Any] = {"messages": [{"role": "user", "content": "hi"}]}
        out1 = chat_mod.chat_agent_node(state, None)  # type: ignore[arg-type]
        merged = list(state["messages"]) + list(out1["messages"])
        out2 = chat_mod.tool_executor_node({"messages": merged})  # type: ignore[arg-type]
        merged2 = merged + list(out2["messages"])
        out3 = chat_mod.chat_agent_node({"messages": merged2}, None)  # type: ignore[arg-type]

    final = out3["messages"][0]
    assert final["role"] == "assistant"
    assert final["content"] == "Done."
    assert mock_client.chat.completions.create.call_count == 2
    second_kw = mock_client.chat.completions.create.call_args_list[1].kwargs["messages"]
    assert any(
        m.get("role") == "tool" and "from-tool" in str(m.get("content", "")) for m in second_kw
    )


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


def test_tool_executor_returns_json_error_on_tool_oserror() -> None:
    """Tools may raise OSError, SDK errors, etc.; node must not crash the graph."""
    flaky = MagicMock()
    flaky.name = "flaky_tool"
    flaky.side_effect = OSError("simulated IO failure")

    with patch("app.nodes.chat.get_registered_tools", return_value=[flaky]):
        out = chat_mod.tool_executor_node(
            {
                "messages": [
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{"id": "c1", "name": "flaky_tool", "args": {}}],
                    }
                ]
            }
        )

    msg = out["messages"][0]
    assert msg["role"] == "tool"
    payload = json.loads(msg["content"])
    assert "error" in payload
    assert "simulated IO failure" in payload["error"]


# ── Codex / unsupported provider path ────────────────────────────────────────


def test_codex_general_node_error_is_plain_dict_assistant_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unsupported provider path must persist dict messages (not LC AIMessage)."""
    monkeypatch.setenv("LLM_PROVIDER", "codex")
    chat_mod.reset_chat_llm_cache()
    out = chat_mod.general_node(
        {"messages": [{"role": "user", "content": "hello"}]}, {"configurable": {}}
    )
    msg = out["messages"][0]
    assert isinstance(msg, dict)
    assert msg.get("role") == "assistant"
    assert isinstance(msg.get("content"), str)
