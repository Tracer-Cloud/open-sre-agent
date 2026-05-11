"""Comprehensive tests for chat_sdk_adapter against OpenAI and Anthropic API contracts.

Coverage map (mirrors the SDK API specs):

OpenAI chat completions
  § response shape         — content, tool_calls, empty choices
  § content=None           — tool-only response has None content → empty string
  § multi-tool             — multiple tool_calls in one response
  § message normalization  — system/user/assistant/tool role mapping; tool name opt-out
  § kwargs forwarded       — model, max_tokens, tools present/absent, messages list
  § retry / resilience     — transient → success, 3 failures → RuntimeError, auth error
  § client lifecycle       — reuse on same key, recreate on key change

Anthropic messages API
  § response shape         — text block, tool_use blocks, mixed text+tool_use
  § system extraction      — leading system → top-level param; multi-system joined
  § message normalization  — alternating turns, tool-result blocks, non-leading system merge
  § kwargs forwarded       — model, max_tokens, tools present/absent, system param
  § empty guard            — messages=[] after system extraction → ValueError
  § retry / resilience     — transient → success, 3 failures → RuntimeError, auth error
  § client lifecycle       — reuse on same key, recreate on key change

State contract
  § ChatMessageModel       — tool_call_id + name accepted (StrictConfigModel no extra-fields)

Response factory strategy
  _openai_response / _anthropic_response build **real SDK Pydantic objects**
  (openai.types.chat.ChatCompletion, anthropic.types.Message) so the adapter is
  exercised against the same attribute shapes the live API returns.  SimpleNamespace
  dummies would hide type mismatches (e.g. arguments: str vs dict, input: dict vs str).

API references used to derive field names and types:
  OpenAI  — https://platform.openai.com/docs/api-reference/chat/object
  Anthropic — https://docs.anthropic.com/en/api/messages
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

# Real SDK Pydantic types — used to build response fixtures that match what the
# live API actually returns, not SimpleNamespace dummies.
import anthropic.types as ant
import openai.types.chat as oai
import openai.types.chat.chat_completion_message_tool_call as oai_tc_mod
import pytest

from app.services.chat_sdk_adapter import (
    _AnthropicChatAdapter,
    _normalize_messages_for_anthropic,
    _normalize_messages_for_openai,
    _OpenAIChatAdapter,
    _split_system_messages,
    messages_to_invocation_dicts,
)

# ── Response factories using real SDK types ────────────────────────────────────
#
# OpenAI:  ChatCompletion → choices[].message (ChatCompletionMessage)
#            .content       str | None
#            .tool_calls    list[ChatCompletionMessageToolCall] | None
#              .id          str
#              .type        "function"
#              .function    Function(.name: str, .arguments: str  ← JSON string)
#
# Anthropic: Message → .content list[TextBlock | ToolUseBlock]
#            TextBlock:   .type="text"     .text: str
#            ToolUseBlock:.type="tool_use" .id: str  .name: str  .input: dict (pre-decoded)


def _openai_response(content: str | None = "", tool_calls: list[dict] | None = None) -> Any:
    """Build a real openai.types.chat.ChatCompletion object."""
    tc_objs: list[oai.ChatCompletionMessageToolCall] = []
    for tc in tool_calls or []:
        fn = oai_tc_mod.Function(name=tc["name"], arguments=json.dumps(tc.get("args", {})))
        tc_objs.append(oai.ChatCompletionMessageToolCall(id=tc["id"], type="function", function=fn))
    message = oai.ChatCompletionMessage(
        role="assistant",
        content=content,
        tool_calls=tc_objs or None,
    )
    return oai.ChatCompletion(
        id="chatcmpl-test",
        choices=[oai.chat_completion.Choice(finish_reason="stop", index=0, message=message)],
        created=1_700_000_000,
        model="gpt-4o",
        object="chat.completion",
    )


def _anthropic_response(text: str = "", tool_uses: list[dict] | None = None) -> ant.Message:
    """Build a real anthropic.types.Message object.

    Anthropic delivers tool input as a **pre-decoded dict**, never a JSON string.
    """
    blocks: list[ant.TextBlock | ant.ToolUseBlock] = []
    if text:
        blocks.append(ant.TextBlock(type="text", text=text))
    for tu in tool_uses or []:
        blocks.append(
            ant.ToolUseBlock(
                type="tool_use", id=tu["id"], name=tu["name"], input=tu.get("args", {})
            )
        )
    return ant.Message(
        id="msg-test",
        content=blocks,
        model="claude-3-5-sonnet-20241022",
        role="assistant",
        stop_reason="end_turn",
        type="message",
        usage=ant.Usage(input_tokens=10, output_tokens=20),
    )


# ══════════════════════════════════════════════════════════════════════════════
# OpenAI adapter
# ══════════════════════════════════════════════════════════════════════════════

# ── § response shape ─────────────────────────────────────────────────────────


def test_openai_plain_text_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    adapter = _OpenAIChatAdapter(model="gpt-4o", with_tools=False)

    with patch("openai.OpenAI") as cls:
        client = MagicMock()
        client.chat.completions.create.return_value = _openai_response("Hello!")
        cls.return_value = client

        turn = adapter.invoke([{"role": "user", "content": "hi"}])

    assert turn == {"content": "Hello!"}


def test_openai_tool_call_response_single(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    adapter = _OpenAIChatAdapter(model="gpt-4o", with_tools=True)
    resp = _openai_response("", [{"id": "c1", "name": "search", "args": {"q": "foo"}}])

    with patch("openai.OpenAI") as cls:
        client = MagicMock()
        client.chat.completions.create.return_value = resp
        cls.return_value = client

        turn = adapter.invoke([{"role": "user", "content": "go"}])

    assert turn["content"] == ""
    assert turn["tool_calls"] == [{"id": "c1", "name": "search", "args": {"q": "foo"}}]


def test_openai_multi_tool_call_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """Multiple tool_calls in a single assistant message must all be returned."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    adapter = _OpenAIChatAdapter(model="gpt-4o", with_tools=True)
    resp = _openai_response(
        "",
        [
            {"id": "c1", "name": "fetch_logs", "args": {"service": "api"}},
            {"id": "c2", "name": "get_metrics", "args": {"window": "1h"}},
        ],
    )

    with patch("openai.OpenAI") as cls:
        client = MagicMock()
        client.chat.completions.create.return_value = resp
        cls.return_value = client

        turn = adapter.invoke([{"role": "user", "content": "analyse"}])

    assert len(turn["tool_calls"]) == 2
    assert turn["tool_calls"][0]["name"] == "fetch_logs"
    assert turn["tool_calls"][1]["name"] == "get_metrics"


def test_openai_content_none_becomes_empty_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """OpenAI returns content=None for tool-only responses; adapter must not crash."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    adapter = _OpenAIChatAdapter(model="gpt-4o", with_tools=True)
    resp = _openai_response(content=None, tool_calls=[{"id": "c1", "name": "t", "args": {}}])

    with patch("openai.OpenAI") as cls:
        client = MagicMock()
        client.chat.completions.create.return_value = resp
        cls.return_value = client

        turn = adapter.invoke([{"role": "user", "content": "go"}])

    assert turn["content"] == ""
    assert turn["tool_calls"][0]["id"] == "c1"


def test_openai_empty_choices_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    adapter = _OpenAIChatAdapter(model="gpt-4o", with_tools=False)
    # Real ChatCompletion cannot be constructed with an empty choices list
    # (the API never does this), so use a minimal stand-in for this error path only.
    from types import SimpleNamespace

    bad_resp = SimpleNamespace(choices=[])

    with patch("openai.OpenAI") as cls:
        client = MagicMock()
        client.chat.completions.create.return_value = bad_resp
        cls.return_value = client

        with pytest.raises(RuntimeError, match="empty choices"):
            adapter.invoke([{"role": "user", "content": "hi"}])


def test_openai_tool_call_invalid_json_args_defaults_to_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed JSON in tool call arguments must not propagate — fall back to {}.

    The real SDK validates `arguments` as a string but not as valid JSON — so this
    edge case (server bug or streaming partial) is represented with real SDK types
    where possible, except the inner Function object which requires a plain stub
    because Pydantic would normalise valid JSON before we can test the bad-JSON path.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    adapter = _OpenAIChatAdapter(model="gpt-4o", with_tools=True)
    from types import SimpleNamespace

    fn = SimpleNamespace(name="broken", arguments="{not valid json}")
    tc = SimpleNamespace(id="bad", type="function", function=fn)
    message = SimpleNamespace(content=None, tool_calls=[tc])
    resp = SimpleNamespace(choices=[SimpleNamespace(message=message)])

    with patch("openai.OpenAI") as cls:
        client = MagicMock()
        client.chat.completions.create.return_value = resp
        cls.return_value = client

        turn = adapter.invoke([{"role": "user", "content": "go"}])

    assert turn["tool_calls"][0]["args"] == {}


# ── § kwargs forwarded ────────────────────────────────────────────────────────


def test_openai_model_and_max_tokens_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    adapter = _OpenAIChatAdapter(model="gpt-4o-mini", with_tools=False)

    with patch("openai.OpenAI") as cls:
        client = MagicMock()
        client.chat.completions.create.return_value = _openai_response("ok")
        cls.return_value = client

        adapter.invoke([{"role": "user", "content": "hi"}])

    _, kw = client.chat.completions.create.call_args
    assert kw["model"] == "gpt-4o-mini"
    assert "max_tokens" in kw


def test_openai_tools_key_absent_when_with_tools_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    adapter = _OpenAIChatAdapter(model="gpt-4o", with_tools=False)

    with patch("openai.OpenAI") as cls:
        client = MagicMock()
        client.chat.completions.create.return_value = _openai_response("ok")
        cls.return_value = client

        adapter.invoke([{"role": "user", "content": "hi"}])

    _, kw = client.chat.completions.create.call_args
    assert "tools" not in kw


def test_openai_tools_key_absent_when_no_registered_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    adapter = _OpenAIChatAdapter(model="gpt-4o", with_tools=True)

    with (
        patch("openai.OpenAI") as cls,
        patch("app.services.chat_sdk_adapter.get_registered_tools", return_value=[]),
    ):
        client = MagicMock()
        client.chat.completions.create.return_value = _openai_response("ok")
        cls.return_value = client

        adapter.invoke([{"role": "user", "content": "hi"}])

    _, kw = client.chat.completions.create.call_args
    assert "tools" not in kw


# ── § retry / resilience ─────────────────────────────────────────────────────


def test_openai_transient_error_retries_and_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    adapter = _OpenAIChatAdapter(model="gpt-4o", with_tools=False)
    success_resp = _openai_response("recovered")

    with patch("openai.OpenAI") as cls, patch("time.sleep"):
        client = MagicMock()
        client.chat.completions.create.side_effect = [
            RuntimeError("timeout"),
            success_resp,
        ]
        cls.return_value = client

        turn = adapter.invoke([{"role": "user", "content": "hi"}])

    assert turn["content"] == "recovered"
    assert client.chat.completions.create.call_count == 2


def test_openai_three_failures_raises_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    adapter = _OpenAIChatAdapter(model="gpt-4o", with_tools=False)

    with patch("openai.OpenAI") as cls, patch("time.sleep"):
        client = MagicMock()
        client.chat.completions.create.side_effect = ConnectionError("network")
        cls.return_value = client

        with pytest.raises(RuntimeError, match="multiple retries"):
            adapter.invoke([{"role": "user", "content": "hi"}])

    assert client.chat.completions.create.call_count == 3


def test_openai_auth_error_raises_immediately_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AuthenticationError must not be retried — surface it immediately."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-bad")
    adapter = _OpenAIChatAdapter(model="gpt-4o", with_tools=False)

    from openai import AuthenticationError as OAIAuthErr

    auth_err = OAIAuthErr("bad key", response=MagicMock(), body={})

    with patch("openai.OpenAI") as cls, patch("time.sleep") as sleep_mock:
        client = MagicMock()
        client.chat.completions.create.side_effect = auth_err
        cls.return_value = client

        with pytest.raises(RuntimeError, match="authentication failed"):
            adapter.invoke([{"role": "user", "content": "hi"}])

    sleep_mock.assert_not_called()
    assert client.chat.completions.create.call_count == 1


# ── § client lifecycle ────────────────────────────────────────────────────────


def test_openai_client_reused_on_same_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-same")
    adapter = _OpenAIChatAdapter(model="gpt-4o", with_tools=False)

    with patch("openai.OpenAI") as cls:
        client = MagicMock()
        client.chat.completions.create.return_value = _openai_response("a")
        cls.return_value = client

        adapter.invoke([{"role": "user", "content": "first"}])
        adapter.invoke([{"role": "user", "content": "second"}])

    assert cls.call_count == 1


def test_openai_client_recreated_on_key_rotation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-first")
    adapter = _OpenAIChatAdapter(model="gpt-4o", with_tools=False)

    with patch("openai.OpenAI") as cls:
        client_a, client_b = MagicMock(), MagicMock()
        client_a.chat.completions.create.return_value = _openai_response("a")
        client_b.chat.completions.create.return_value = _openai_response("b")
        cls.side_effect = [client_a, client_b]

        adapter.invoke([{"role": "user", "content": "first"}])
        monkeypatch.setenv("OPENAI_API_KEY", "sk-rotated")
        adapter.invoke([{"role": "user", "content": "second"}])

    assert cls.call_count == 2


# ══════════════════════════════════════════════════════════════════════════════
# OpenAI message normalisation
# ══════════════════════════════════════════════════════════════════════════════


def test_openai_normalize_multi_turn_conversation() -> None:
    """[system, user, assistant, tool, user] must map cleanly to OpenAI shape."""
    msgs = [
        {"role": "system", "content": "You help."},
        {"role": "user", "content": "query"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "name": "fetch", "args": {"x": 1}}],
        },
        {"role": "tool", "content": "result", "tool_call_id": "c1", "name": "fetch"},
        {"role": "user", "content": "thanks"},
    ]
    out = _normalize_messages_for_openai(msgs)

    roles = [m["role"] for m in out]
    assert roles == ["system", "user", "assistant", "tool", "user"]

    # tool_calls on assistant converted to OpenAI function shape with JSON arguments
    assistant = out[2]
    assert "tool_calls" in assistant
    tc = assistant["tool_calls"][0]
    assert tc["type"] == "function"
    assert json.loads(tc["function"]["arguments"]) == {"x": 1}

    # tool message keeps name and tool_call_id
    tool_msg = out[3]
    assert tool_msg["tool_call_id"] == "c1"
    assert tool_msg["name"] == "fetch"


def test_openai_normalize_tool_message_without_name_omits_name_key() -> None:
    """Tool messages with no name must not include the 'name' key."""
    out = _normalize_messages_for_openai([{"role": "tool", "content": "res", "tool_call_id": "c1"}])
    assert "name" not in out[0]


def test_openai_normalize_assistant_tool_calls_arguments_are_json_string() -> None:
    """OpenAI expects arguments as a JSON string, not a dict."""
    out = _normalize_messages_for_openai(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "name": "do_thing", "args": {"a": 1, "b": "two"}}],
            }
        ]
    )
    raw_args = out[0]["tool_calls"][0]["function"]["arguments"]
    assert isinstance(raw_args, str)
    assert json.loads(raw_args) == {"a": 1, "b": "two"}


# ══════════════════════════════════════════════════════════════════════════════
# Anthropic adapter
# ══════════════════════════════════════════════════════════════════════════════

# ── § response shape ─────────────────────────────────────────────────────────


def test_anthropic_plain_text_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    adapter = _AnthropicChatAdapter(model="claude-3-5-sonnet-20241022", with_tools=False)

    with patch("anthropic.Anthropic") as cls:
        client = MagicMock()
        client.messages.create.return_value = _anthropic_response(text="Hi there.")
        cls.return_value = client

        turn = adapter.invoke([{"role": "user", "content": "hello"}])

    assert turn == {"content": "Hi there."}


def test_anthropic_multi_text_blocks_concatenated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Multiple text blocks in a single response must be joined into one content string."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    adapter = _AnthropicChatAdapter(model="claude-3-5-sonnet-20241022", with_tools=False)
    resp = ant.Message(
        id="msg-multi",
        content=[
            ant.TextBlock(type="text", text="Part one. "),
            ant.TextBlock(type="text", text="Part two."),
        ],
        model="claude-3-5-sonnet-20241022",
        role="assistant",
        stop_reason="end_turn",
        type="message",
        usage=ant.Usage(input_tokens=5, output_tokens=10),
    )

    with patch("anthropic.Anthropic") as cls:
        client = MagicMock()
        client.messages.create.return_value = resp
        cls.return_value = client

        turn = adapter.invoke([{"role": "user", "content": "write"}])

    assert turn["content"] == "Part one. Part two."


def test_anthropic_single_tool_use_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    adapter = _AnthropicChatAdapter(model="claude-3-5-sonnet-20241022", with_tools=True)

    with patch("anthropic.Anthropic") as cls:
        client = MagicMock()
        client.messages.create.return_value = _anthropic_response(
            tool_uses=[{"id": "tu1", "name": "search", "args": {"query": "opensre"}}]
        )
        cls.return_value = client

        turn = adapter.invoke([{"role": "user", "content": "search it"}])

    assert turn["content"] == ""
    assert turn["tool_calls"] == [{"id": "tu1", "name": "search", "args": {"query": "opensre"}}]


def test_anthropic_multi_tool_use_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """Multiple tool_use blocks must all be returned as separate ToolCallPayloads."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    adapter = _AnthropicChatAdapter(model="claude-3-5-sonnet-20241022", with_tools=True)

    with patch("anthropic.Anthropic") as cls:
        client = MagicMock()
        client.messages.create.return_value = _anthropic_response(
            tool_uses=[
                {"id": "tu1", "name": "fetch_logs", "args": {"svc": "api"}},
                {"id": "tu2", "name": "get_metrics", "args": {"win": "1h"}},
            ]
        )
        cls.return_value = client

        turn = adapter.invoke([{"role": "user", "content": "analyse"}])

    assert len(turn["tool_calls"]) == 2
    assert turn["tool_calls"][0] == {"id": "tu1", "name": "fetch_logs", "args": {"svc": "api"}}
    assert turn["tool_calls"][1] == {"id": "tu2", "name": "get_metrics", "args": {"win": "1h"}}


def test_anthropic_mixed_text_and_tool_use(monkeypatch: pytest.MonkeyPatch) -> None:
    """Text + tool_use in one response: content has the text, tool_calls has the tools."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    adapter = _AnthropicChatAdapter(model="claude-3-5-sonnet-20241022", with_tools=True)

    with patch("anthropic.Anthropic") as cls:
        client = MagicMock()
        client.messages.create.return_value = _anthropic_response(
            text="Let me check that.",
            tool_uses=[{"id": "tu1", "name": "lookup", "args": {}}],
        )
        cls.return_value = client

        turn = adapter.invoke([{"role": "user", "content": "go"}])

    assert turn["content"] == "Let me check that."
    assert len(turn["tool_calls"]) == 1
    assert turn["tool_calls"][0]["name"] == "lookup"


def test_anthropic_tool_input_is_pre_decoded_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    """Anthropic sends tool input as a pre-decoded dict (not a JSON string); args must match."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    adapter = _AnthropicChatAdapter(model="claude-3-5-sonnet-20241022", with_tools=True)
    resp = ant.Message(
        id="msg-dict",
        content=[ant.ToolUseBlock(type="tool_use", id="x", name="t", input={"nested": {"k": "v"}})],
        model="claude-3-5-sonnet-20241022",
        role="assistant",
        stop_reason="tool_use",
        type="message",
        usage=ant.Usage(input_tokens=5, output_tokens=5),
    )

    with patch("anthropic.Anthropic") as cls:
        client = MagicMock()
        client.messages.create.return_value = resp
        cls.return_value = client

        turn = adapter.invoke([{"role": "user", "content": "run"}])

    assert turn["tool_calls"][0]["args"] == {"nested": {"k": "v"}}


# ── § system extraction ───────────────────────────────────────────────────────


def test_anthropic_multiple_leading_system_messages_joined(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multiple leading system messages must be joined with \\n into top-level `system`."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    adapter = _AnthropicChatAdapter(model="claude-3-5-sonnet-20241022", with_tools=False)

    with patch("anthropic.Anthropic") as cls:
        client = MagicMock()
        client.messages.create.return_value = _anthropic_response(text="ok")
        cls.return_value = client

        adapter.invoke(
            [
                {"role": "system", "content": "Rule one."},
                {"role": "system", "content": "Rule two."},
                {"role": "user", "content": "hi"},
            ]
        )

    _, kw = client.messages.create.call_args
    assert kw["system"] == "Rule one.\nRule two."
    for m in kw["messages"]:
        assert m["role"] != "system"


def test_anthropic_no_system_message_omits_system_kwarg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    adapter = _AnthropicChatAdapter(model="claude-3-5-sonnet-20241022", with_tools=False)

    with patch("anthropic.Anthropic") as cls:
        client = MagicMock()
        client.messages.create.return_value = _anthropic_response(text="ok")
        cls.return_value = client

        adapter.invoke([{"role": "user", "content": "hi"}])

    _, kw = client.messages.create.call_args
    assert "system" not in kw


# ── § kwargs forwarded ────────────────────────────────────────────────────────


def test_anthropic_model_and_max_tokens_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    adapter = _AnthropicChatAdapter(model="claude-3-haiku-20240307", with_tools=False)

    with patch("anthropic.Anthropic") as cls:
        client = MagicMock()
        client.messages.create.return_value = _anthropic_response(text="ok")
        cls.return_value = client

        adapter.invoke([{"role": "user", "content": "hi"}])

    _, kw = client.messages.create.call_args
    assert kw["model"] == "claude-3-haiku-20240307"
    assert "max_tokens" in kw


def test_anthropic_tools_key_absent_when_with_tools_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    adapter = _AnthropicChatAdapter(model="claude-3-5-sonnet-20241022", with_tools=False)

    with patch("anthropic.Anthropic") as cls:
        client = MagicMock()
        client.messages.create.return_value = _anthropic_response(text="ok")
        cls.return_value = client

        adapter.invoke([{"role": "user", "content": "hi"}])

    _, kw = client.messages.create.call_args
    assert "tools" not in kw


def test_anthropic_tools_key_absent_when_no_registered_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    adapter = _AnthropicChatAdapter(model="claude-3-5-sonnet-20241022", with_tools=True)

    with (
        patch("anthropic.Anthropic") as cls,
        patch("app.services.chat_sdk_adapter.get_registered_tools", return_value=[]),
    ):
        client = MagicMock()
        client.messages.create.return_value = _anthropic_response(text="ok")
        cls.return_value = client

        adapter.invoke([{"role": "user", "content": "hi"}])

    _, kw = client.messages.create.call_args
    assert "tools" not in kw


# ── § empty guard ─────────────────────────────────────────────────────────────


def test_anthropic_empty_messages_raises_before_api_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    adapter = _AnthropicChatAdapter(model="claude-3-5-sonnet-20241022", with_tools=False)

    with patch("anthropic.Anthropic") as cls:
        client = MagicMock()
        cls.return_value = client

        with pytest.raises(ValueError, match="empty messages list"):
            adapter.invoke([])

    client.messages.create.assert_not_called()


def test_anthropic_system_only_messages_raises_before_api_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    adapter = _AnthropicChatAdapter(model="claude-3-5-sonnet-20241022", with_tools=False)

    with patch("anthropic.Anthropic") as cls:
        client = MagicMock()
        cls.return_value = client

        with pytest.raises(ValueError, match="empty messages list"):
            adapter.invoke([{"role": "system", "content": "Prompt."}])

    client.messages.create.assert_not_called()


# ── § retry / resilience ─────────────────────────────────────────────────────


def test_anthropic_transient_error_retries_and_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    adapter = _AnthropicChatAdapter(model="claude-3-5-sonnet-20241022", with_tools=False)

    with patch("anthropic.Anthropic") as cls, patch("time.sleep"):
        client = MagicMock()
        client.messages.create.side_effect = [
            ConnectionError("timeout"),
            _anthropic_response(text="recovered"),
        ]
        cls.return_value = client

        turn = adapter.invoke([{"role": "user", "content": "hi"}])

    assert turn["content"] == "recovered"
    assert client.messages.create.call_count == 2


def test_anthropic_three_failures_raises_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    adapter = _AnthropicChatAdapter(model="claude-3-5-sonnet-20241022", with_tools=False)

    with patch("anthropic.Anthropic") as cls, patch("time.sleep"):
        client = MagicMock()
        client.messages.create.side_effect = ConnectionError("network")
        cls.return_value = client

        with pytest.raises(RuntimeError, match="multiple retries"):
            adapter.invoke([{"role": "user", "content": "hi"}])

    assert client.messages.create.call_count == 3


def test_anthropic_auth_error_raises_immediately_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-bad")
    adapter = _AnthropicChatAdapter(model="claude-3-5-sonnet-20241022", with_tools=False)

    from anthropic import AuthenticationError as AntAuthErr

    auth_err = AntAuthErr("bad key", response=MagicMock(), body={})

    with patch("anthropic.Anthropic") as cls, patch("time.sleep") as sleep_mock:
        client = MagicMock()
        client.messages.create.side_effect = auth_err
        cls.return_value = client

        with pytest.raises(RuntimeError, match="authentication failed"):
            adapter.invoke([{"role": "user", "content": "hi"}])

    sleep_mock.assert_not_called()
    assert client.messages.create.call_count == 1


# ── § client lifecycle ────────────────────────────────────────────────────────


def test_anthropic_client_reused_on_same_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-same")
    adapter = _AnthropicChatAdapter(model="claude-3-5-sonnet-20241022", with_tools=False)

    with patch("anthropic.Anthropic") as cls:
        client = MagicMock()
        client.messages.create.return_value = _anthropic_response(text="ok")
        cls.return_value = client

        adapter.invoke([{"role": "user", "content": "first"}])
        adapter.invoke([{"role": "user", "content": "second"}])

    assert cls.call_count == 1


def test_anthropic_client_recreated_on_key_rotation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-first")
    adapter = _AnthropicChatAdapter(model="claude-3-5-sonnet-20241022", with_tools=False)

    with patch("anthropic.Anthropic") as cls:
        ca, cb = MagicMock(), MagicMock()
        ca.messages.create.return_value = _anthropic_response(text="a")
        cb.messages.create.return_value = _anthropic_response(text="b")
        cls.side_effect = [ca, cb]

        adapter.invoke([{"role": "user", "content": "first"}])
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-rotated")
        adapter.invoke([{"role": "user", "content": "second"}])

    assert cls.call_count == 2


# ══════════════════════════════════════════════════════════════════════════════
# Anthropic message normalisation
# ══════════════════════════════════════════════════════════════════════════════


def test_anthropic_normalize_full_multi_turn_conversation() -> None:
    """[system(extracted), user, assistant+tools, tool-results, user] must produce valid turns."""
    # system is stripped before _normalize_messages_for_anthropic is called;
    # what we test here is what _AnthropicChatAdapter.invoke sends to the API.
    _, non_system = _split_system_messages(
        [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "search for cats"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "tu1", "name": "search", "args": {"q": "cats"}}],
            },
            {"role": "tool", "content": "10 results", "tool_call_id": "tu1"},
            {"role": "user", "content": "show me first"},
        ]
    )
    out = _normalize_messages_for_anthropic(non_system)
    roles = [m["role"] for m in out]

    # No consecutive user turns
    for i in range(len(roles) - 1):
        assert not (roles[i] == "user" and roles[i + 1] == "user"), (
            f"Consecutive user turns at positions {i},{i + 1}: {roles}"
        )

    # No consecutive assistant turns
    for i in range(len(roles) - 1):
        assert not (roles[i] == "assistant" and roles[i + 1] == "assistant"), (
            f"Consecutive assistant turns at positions {i},{i + 1}: {roles}"
        )


def test_anthropic_normalize_assistant_with_tool_calls_becomes_content_blocks() -> None:
    """An assistant message with tool_calls must become a content-block list."""
    out = _normalize_messages_for_anthropic(
        [
            {
                "role": "assistant",
                "content": "Let me look.",
                "tool_calls": [{"id": "t1", "name": "fetch", "args": {"url": "http://x"}}],
            }
        ]
    )
    assert out[0]["role"] == "assistant"
    blocks = out[0]["content"]
    assert isinstance(blocks, list)
    types = {b["type"] for b in blocks}
    assert "text" in types
    assert "tool_use" in types


def test_anthropic_normalize_tool_result_uses_correct_field_name() -> None:
    """Tool results use tool_use_id (not tool_call_id) in Anthropic's format."""
    out = _normalize_messages_for_anthropic(
        [{"role": "tool", "content": "result", "tool_call_id": "c1"}]
    )
    block = out[0]["content"][0]
    assert block["type"] == "tool_result"
    assert block["tool_use_id"] == "c1"
    assert "tool_call_id" not in block


def test_anthropic_normalize_no_consecutive_user_turns_for_any_ordering() -> None:
    """[assistant, system, user] must not produce two consecutive user turns."""
    out = _normalize_messages_for_anthropic(
        [
            {"role": "assistant", "content": "ok"},
            {"role": "system", "content": "injected"},
            {"role": "user", "content": "next"},
        ]
    )
    roles = [m["role"] for m in out]
    for i in range(len(roles) - 1):
        assert not (roles[i] == "user" and roles[i + 1] == "user"), f"Adjacent users: {roles}"


# ══════════════════════════════════════════════════════════════════════════════
# messages_to_invocation_dicts — neutral graph message normalization
# ══════════════════════════════════════════════════════════════════════════════


def test_messages_to_invocation_dicts_plain_dicts_pass_through() -> None:
    msgs: list[Any] = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    out = messages_to_invocation_dicts(msgs)
    assert out == msgs


def test_messages_to_invocation_dicts_lc_type_field_remapped() -> None:
    """Dict messages using legacy ``type`` field (human/ai) must be remapped to ``role``."""
    msgs: list[Any] = [
        {"type": "human", "content": "hi"},
        {"type": "ai", "content": "hello"},
    ]
    out = messages_to_invocation_dicts(msgs)
    assert out[0]["role"] == "user"
    assert out[1]["role"] == "assistant"


def test_messages_to_invocation_dicts_preserves_explicit_empty_tool_calls() -> None:
    msgs: list[Any] = [{"role": "assistant", "content": "x", "tool_calls": []}]
    out = messages_to_invocation_dicts(msgs)
    assert out[0].get("tool_calls") == []


def test_messages_to_invocation_dicts_tool_shaped_object() -> None:
    class _Toolish:
        type = "tool"
        content = '{"r": 1}'
        tool_call_id = "z"
        name = "n"

    out = messages_to_invocation_dicts([_Toolish()])
    assert out[0] == {
        "role": "tool",
        "content": '{"r": 1}',
        "tool_call_id": "z",
        "name": "n",
    }


# ══════════════════════════════════════════════════════════════════════════════
# Non-retryable error handling in retry helpers
# ══════════════════════════════════════════════════════════════════════════════


def test_openai_not_found_raises_immediately_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NotFoundError (wrong model name) must surface immediately without retry."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    adapter = _OpenAIChatAdapter(model="gpt-nonexistent", with_tools=False)

    from openai import NotFoundError as OAINotFoundErr

    not_found_err = OAINotFoundErr("model not found", response=MagicMock(), body={})

    with patch("openai.OpenAI") as cls, patch("time.sleep") as sleep_mock:
        client = MagicMock()
        client.chat.completions.create.side_effect = not_found_err
        cls.return_value = client

        with pytest.raises(RuntimeError, match="not found"):
            adapter.invoke([{"role": "user", "content": "hi"}])

    sleep_mock.assert_not_called()
    assert client.chat.completions.create.call_count == 1


def test_openai_bad_request_raises_immediately_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BadRequestError (billing/invalid request) must surface immediately without retry."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    adapter = _OpenAIChatAdapter(model="gpt-4o", with_tools=False)

    from openai import BadRequestError as OAIBadReqErr

    bad_req_err = OAIBadReqErr("overdue payment", response=MagicMock(), body={})

    with patch("openai.OpenAI") as cls, patch("time.sleep") as sleep_mock:
        client = MagicMock()
        client.chat.completions.create.side_effect = bad_req_err
        cls.return_value = client

        with pytest.raises(RuntimeError, match="400"):
            adapter.invoke([{"role": "user", "content": "hi"}])

    sleep_mock.assert_not_called()
    assert client.chat.completions.create.call_count == 1


def test_openai_insufficient_quota_raises_immediately_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RateLimitError with insufficient_quota code must raise immediately (billing limit)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    adapter = _OpenAIChatAdapter(model="gpt-4o", with_tools=False)

    from openai import RateLimitError as OAIRateLimitErr

    quota_err = OAIRateLimitErr("quota exceeded", response=MagicMock(), body={})
    quota_err.body = {"error": {"code": "insufficient_quota"}}

    with patch("openai.OpenAI") as cls, patch("time.sleep") as sleep_mock:
        client = MagicMock()
        client.chat.completions.create.side_effect = quota_err
        cls.return_value = client

        with pytest.raises(RuntimeError, match="quota"):
            adapter.invoke([{"role": "user", "content": "hi"}])

    sleep_mock.assert_not_called()
    assert client.chat.completions.create.call_count == 1


def test_anthropic_not_found_raises_immediately_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anthropic NotFoundError must surface immediately without retry."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    adapter = _AnthropicChatAdapter(model="claude-nonexistent", with_tools=False)

    from anthropic import NotFoundError as AntNotFoundErr

    not_found_err = AntNotFoundErr("model not found", response=MagicMock(), body={})

    with patch("anthropic.Anthropic") as cls, patch("time.sleep") as sleep_mock:
        client = MagicMock()
        client.messages.create.side_effect = not_found_err
        cls.return_value = client

        with pytest.raises(RuntimeError, match="not found"):
            adapter.invoke([{"role": "user", "content": "hi"}])

    sleep_mock.assert_not_called()
    assert client.messages.create.call_count == 1


def test_anthropic_bad_request_raises_immediately_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anthropic BadRequestError (e.g. credit balance too low) must not retry."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    adapter = _AnthropicChatAdapter(model="claude-3-5-sonnet-20241022", with_tools=False)

    from anthropic import BadRequestError as AntBadReqErr

    bad_req_err = AntBadReqErr("credit balance too low", response=MagicMock(), body={})

    with patch("anthropic.Anthropic") as cls, patch("time.sleep") as sleep_mock:
        client = MagicMock()
        client.messages.create.side_effect = bad_req_err
        cls.return_value = client

        with pytest.raises(RuntimeError, match="400"):
            adapter.invoke([{"role": "user", "content": "hi"}])

    sleep_mock.assert_not_called()
    assert client.messages.create.call_count == 1


def test_anthropic_permission_denied_raises_immediately_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anthropic PermissionDeniedError must surface immediately without retry."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    adapter = _AnthropicChatAdapter(model="claude-3-5-sonnet-20241022", with_tools=False)

    from anthropic import PermissionDeniedError as AntPermDeniedErr

    perm_err = AntPermDeniedErr("access denied", response=MagicMock(), body={})

    with patch("anthropic.Anthropic") as cls, patch("time.sleep") as sleep_mock:
        client = MagicMock()
        client.messages.create.side_effect = perm_err
        cls.return_value = client

        with pytest.raises(RuntimeError, match="access denied"):
            adapter.invoke([{"role": "user", "content": "hi"}])

    sleep_mock.assert_not_called()
    assert client.messages.create.call_count == 1
