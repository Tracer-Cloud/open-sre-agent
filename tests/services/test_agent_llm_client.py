from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from app.services.agent_llm_client import (
    AnthropicAgentClient,
    BedrockAgentClient,
    OpenAIAgentClient,
)


def _install_fake_anthropic(monkeypatch: pytest.MonkeyPatch) -> types.SimpleNamespace:
    fake_module = types.SimpleNamespace()

    class AuthenticationError(Exception):
        pass

    class BadRequestError(Exception):
        def __init__(self, message: str) -> None:
            super().__init__(message)
            self.message = message

    class NotFoundError(Exception):
        pass

    class PermissionDeniedError(Exception):
        pass

    class Anthropic:
        def __init__(self, **_: object) -> None:
            self.messages = types.SimpleNamespace(create=lambda **_: None)

    class AnthropicBedrock:
        def __init__(self, **_: object) -> None:
            self.messages = types.SimpleNamespace(create=lambda **_: None)

    fake_module.AuthenticationError = AuthenticationError
    fake_module.BadRequestError = BadRequestError
    fake_module.NotFoundError = NotFoundError
    fake_module.PermissionDeniedError = PermissionDeniedError
    fake_module.Anthropic = Anthropic
    fake_module.AnthropicBedrock = AnthropicBedrock
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)
    return fake_module


def test_bedrock_client_requires_region_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_anthropic(monkeypatch)
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)

    with pytest.raises(RuntimeError, match="Bedrock requires AWS_REGION or AWS_DEFAULT_REGION"):
        BedrockAgentClient(model="us.anthropic.claude-sonnet-4-6")


def test_bedrock_auth_error_message_references_aws_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_anthropic = _install_fake_anthropic(monkeypatch)
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    client = BedrockAgentClient(model="us.anthropic.claude-sonnet-4-6")

    def raise_auth_error(**_: object) -> object:
        raise fake_anthropic.AuthenticationError("expired")

    client._client = types.SimpleNamespace(messages=types.SimpleNamespace(create=raise_auth_error))

    with pytest.raises(RuntimeError) as exc:
        client.invoke(messages=[{"role": "user", "content": "hi"}])

    message = str(exc.value)
    assert "Bedrock authentication failed" in message
    assert "AWS credentials" in message
    assert "ANTHROPIC_API_KEY" not in message


def test_bedrock_permission_denied_is_not_retried_and_mentions_marketplace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_anthropic = _install_fake_anthropic(monkeypatch)
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    client = BedrockAgentClient(model="us.anthropic.claude-sonnet-4-6")
    calls = 0

    def raise_permission_denied(**_: object) -> object:
        nonlocal calls
        calls += 1
        raise fake_anthropic.PermissionDeniedError("marketplace denied")

    client._client = types.SimpleNamespace(
        messages=types.SimpleNamespace(create=raise_permission_denied)
    )

    with pytest.raises(RuntimeError) as exc:
        client.invoke(messages=[{"role": "user", "content": "hi"}])

    message = str(exc.value)
    assert calls == 1
    assert "Bedrock model 'us.anthropic.claude-sonnet-4-6' is not available" in message
    assert "AWS Marketplace" in message
    assert "aws-marketplace:ViewSubscriptions" in message
    assert "aws-marketplace:Subscribe" in message


def _install_fake_openai(monkeypatch: pytest.MonkeyPatch) -> types.SimpleNamespace:
    fake_module = types.SimpleNamespace()

    class AuthenticationError(Exception):
        pass

    class BadRequestError(Exception):
        pass

    class NotFoundError(Exception):
        pass

    class OpenAI:
        def __init__(self, **_: object) -> None:
            self.chat = types.SimpleNamespace(
                completions=types.SimpleNamespace(create=lambda **_: None)
            )

    fake_module.AuthenticationError = AuthenticationError
    fake_module.BadRequestError = BadRequestError
    fake_module.NotFoundError = NotFoundError
    fake_module.OpenAI = OpenAI
    monkeypatch.setitem(sys.modules, "openai", fake_module)
    return fake_module


def _make_fake_openai_response(
    *,
    content: str = "",
    tool_calls: list[types.SimpleNamespace] | None = None,
    finish_reason: str = "stop",
    extra_msg_fields: dict | None = None,
) -> types.SimpleNamespace:
    """Build a fake OpenAI chat completion response.

    model_dump() mirrors the real SDK: every pydantic field is present,
    including the null ones (refusal, audio, function_call).  This lets
    tests verify that exclude_none=True strips those nulls before the
    dict is stored in raw_content.
    """

    def model_dump(*, exclude_none: bool = False) -> dict:
        # Simulate the full SDK field set, nulls included.
        result: dict = {
            "role": "assistant",
            "content": content or None,
            "refusal": None,  # SDK null field
            "audio": None,  # SDK null field
            "function_call": None,  # SDK null field
        }
        if tool_calls:
            result["tool_calls"] = [tc.model_dump() for tc in tool_calls]
        if extra_msg_fields:
            result.update(extra_msg_fields)
        if exclude_none:
            result = {k: v for k, v in result.items() if v is not None}
        return result

    msg = types.SimpleNamespace(
        content=content or None,
        tool_calls=tool_calls,
        model_dump=model_dump,
    )
    choice = types.SimpleNamespace(message=msg, finish_reason=finish_reason)
    return types.SimpleNamespace(choices=[choice])


def test_openai_agent_client_invoke_sets_raw_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """raw_content must be the serialized API message so providers like Gemini
    can echo back provider-specific fields (e.g. thought_signature) on the
    next turn."""
    fake_openai = _install_fake_openai(monkeypatch)

    client = OpenAIAgentClient.__new__(OpenAIAgentClient)
    fake_response = _make_fake_openai_response(content="hello")
    client._client = types.SimpleNamespace(
        chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=lambda **_: fake_response)
        )
    )
    client._model = "gemini-2.5-flash"
    client._max_tokens = 1024

    del fake_openai  # unused; just ensures the fake module is in sys.modules

    response = client.invoke(messages=[{"role": "user", "content": "hi"}])

    assert response.raw_content is not None
    assert isinstance(response.raw_content, dict)
    assert response.raw_content.get("role") == "assistant"
    # exclude_none=True must strip SDK null fields so they don't
    # cause 400s on Gemini's strict endpoint on the next turn.
    assert "refusal" not in response.raw_content
    assert "audio" not in response.raw_content
    assert "function_call" not in response.raw_content


def test_openai_agent_client_invoke_raw_content_preserves_extra_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Extra fields from the provider (e.g. Gemini thought_signature inside a
    tool call) must survive through raw_content into the next turn's message."""
    _install_fake_openai(monkeypatch)

    def fake_tc_model_dump() -> dict:
        return {
            "id": "call_1",
            "type": "function",
            "function": {"name": "get_logs", "arguments": "{}"},
            "thought_signature": "abc123",  # Gemini extension
        }

    fake_tc = types.SimpleNamespace(
        id="call_1",
        function=types.SimpleNamespace(name="get_logs", arguments="{}"),
        model_dump=fake_tc_model_dump,
    )
    fake_response = _make_fake_openai_response(tool_calls=[fake_tc])

    client = OpenAIAgentClient.__new__(OpenAIAgentClient)
    client._client = types.SimpleNamespace(
        chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=lambda **_: fake_response)
        )
    )
    client._model = "gemini-2.5-flash"
    client._max_tokens = 1024

    response = client.invoke(messages=[{"role": "user", "content": "hi"}])

    assert response.raw_content is not None
    assert isinstance(response.raw_content.get("tool_calls"), list)
    first_tc = response.raw_content["tool_calls"][0]
    assert first_tc.get("thought_signature") == "abc123"


@pytest.mark.parametrize(
    "model,expected_key",
    [
        ("o1", "max_completion_tokens"),
        ("o1-mini", "max_completion_tokens"),
        ("o1-preview", "max_completion_tokens"),
        ("o3", "max_completion_tokens"),
        ("o3-mini", "max_completion_tokens"),
        ("o4-mini", "max_completion_tokens"),
        ("o5-mini", "max_completion_tokens"),  # future model covered by regex
        ("gpt-4o", "max_tokens"),
        ("gemini-2.5-flash", "max_tokens"),
    ],
)
def test_openai_agent_client_uses_correct_tokens_param(
    monkeypatch: pytest.MonkeyPatch,
    model: str,
    expected_key: str,
) -> None:
    """O-series reasoning models require max_completion_tokens; others use max_tokens."""
    _install_fake_openai(monkeypatch)

    captured: dict[str, object] = {}

    def fake_create(**kwargs: object) -> object:
        captured.update(kwargs)
        return _make_fake_openai_response(content="ok")

    client = OpenAIAgentClient.__new__(OpenAIAgentClient)
    client._client = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=fake_create))
    )
    client._model = model
    client._max_tokens = 512

    client.invoke(messages=[{"role": "user", "content": "hi"}])

    assert expected_key in captured, f"expected '{expected_key}' in kwargs for model {model!r}"
    other_key = "max_tokens" if expected_key == "max_completion_tokens" else "max_completion_tokens"
    assert other_key not in captured, f"unexpected '{other_key}' in kwargs for model {model!r}"


def test_sdk_type_error_for_missing_api_key_fails_fast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_anthropic(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    call_count = 0

    def raise_auth_type_error(**_: object) -> object:
        nonlocal call_count
        call_count += 1
        raise TypeError(
            "Could not resolve authentication method. Expected one of api_key, auth_token, "
            "or credentials to be set. Or for one of the `X-Api-Key` or `Authorization` "
            "headers to be explicitly omitted"
        )

    client = AnthropicAgentClient(model="claude-sonnet-4-6")
    client._client = types.SimpleNamespace(
        messages=types.SimpleNamespace(create=raise_auth_type_error)
    )

    with pytest.raises(RuntimeError) as exc:
        client.invoke(messages=[{"role": "user", "content": "hi"}])

    assert call_count == 1, "auth TypeError should not be retried"
    message = str(exc.value)
    assert "authentication failed" in message.lower()
    assert "ANTHROPIC_API_KEY" in message


def test_unrelated_type_error_is_retried_and_wrapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_anthropic(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr("app.services.agent_llm_client.time.sleep", lambda _: None)

    call_count = 0

    def raise_unrelated_type_error(**_: object) -> object:
        nonlocal call_count
        call_count += 1
        raise TypeError("unexpected argument 'foo'")

    client = AnthropicAgentClient(model="claude-sonnet-4-6")
    client._client = types.SimpleNamespace(
        messages=types.SimpleNamespace(create=raise_unrelated_type_error)
    )

    with pytest.raises(RuntimeError, match="API failed after 3 attempts"):
        client.invoke(messages=[{"role": "user", "content": "hi"}])

    assert call_count == 3, "non-auth TypeError should be retried like a generic exception"


@pytest.mark.parametrize(
    "provider", ["codex", "opencode", "claude-code", "kimi", "cursor", "gemini-cli", "copilot"]
)
def test_get_agent_llm_returns_cli_backed_client_for_cli_providers(
    provider: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.agent_llm_client import (
        CLIBackedAgentClient,
        get_agent_llm,
        reset_agent_client,
    )

    monkeypatch.setenv("LLM_PROVIDER", provider)
    reset_agent_client()
    client = get_agent_llm()
    assert isinstance(client, CLIBackedAgentClient), (
        f"Expected CLIBackedAgentClient for provider={provider!r}, got {type(client).__name__}"
    )


def test_cli_backed_agent_client_tool_call_parsing() -> None:
    """CLIBackedAgentClient correctly parses a JSON tool_calls response."""
    import types as _types

    from app.services.agent_llm_client import CLIBackedAgentClient

    fake_adapter = _types.SimpleNamespace(
        name="codex",
        binary_env_key="CODEX_BIN",
        install_hint="npm i -g @openai/codex",
        auth_hint="codex login",
        default_exec_timeout_sec=30.0,
        detect=lambda: _types.SimpleNamespace(
            installed=True, bin_path="/usr/bin/codex", logged_in=True, detail=""
        ),
        build=lambda **kw: _types.SimpleNamespace(
            argv=("/usr/bin/codex", "exec", "-"),
            stdin=kw.get("prompt", ""),
            cwd="/",
            env=None,
            timeout_sec=30.0,
        ),
        parse=lambda **kw: kw.get("stdout", ""),
        explain_failure=lambda **kw: f"exit {kw.get('returncode')}",
    )
    client = CLIBackedAgentClient(fake_adapter, model=None)

    # Patch CLIBackedLLMClient.invoke to return a known JSON response.
    import unittest.mock as mock

    from app.services.llm_client import LLMResponse

    json_response = '{"tool_calls": [{"id": "c1", "name": "my_tool", "input": {"x": 1}}]}'
    with mock.patch(
        "app.integrations.llm_cli.runner.CLIBackedLLMClient.invoke",
        return_value=LLMResponse(content=json_response),
    ):
        result = client.invoke([{"role": "user", "content": "investigate"}])

    assert result.has_tool_calls
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "my_tool"
    assert result.tool_calls[0].input == {"x": 1}
    assert result.content == ""


def test_cli_backed_agent_client_build_assistant_message_includes_tool_json() -> None:
    """Assistant history must retain tool_calls JSON for multi-turn CLI prompts."""
    from app.services.agent_llm_client import CLIBackedAgentClient, ToolCall

    msg = CLIBackedAgentClient.build_assistant_message(
        "",
        [ToolCall(id="t1", name="query_logs", input={"q": "error"})],
    )
    assert msg["role"] == "assistant"
    assert "query_logs" in msg["content"]
    assert '"tool_calls"' in msg["content"]
    assert "t1" in msg["content"]


def test_try_parse_tool_call_json_uses_raw_decode_not_greedy_brace_span() -> None:
    """Trailing brace-containing prose after valid JSON must not drop tool_calls."""
    from app.services import agent_llm_client as alc

    text = '{"tool_calls": [{"id": "a", "name": "t1", "input": {}}]} Here\'s context: {not json}'
    parsed = alc._try_parse_tool_call_json(text)
    assert parsed is not None
    assert len(parsed["tool_calls"]) == 1
    assert parsed["tool_calls"][0]["name"] == "t1"


def test_cli_backed_agent_client_reuses_single_cli_llm_client() -> None:
    """CLIBackedLLMClient should be constructed once so probe cache spans invokes."""
    import types as _types
    import unittest.mock as mock

    from app.integrations.llm_cli.runner import CLIBackedLLMClient
    from app.services.agent_llm_client import CLIBackedAgentClient
    from app.services.llm_client import LLMResponse

    fake_adapter = _types.SimpleNamespace(
        name="codex",
        binary_env_key="CODEX_BIN",
        install_hint="",
        auth_hint="codex login",
        default_exec_timeout_sec=30.0,
        detect=lambda: _types.SimpleNamespace(
            installed=True, bin_path="/usr/bin/codex", logged_in=True, detail=""
        ),
        build=lambda **_kw: _types.SimpleNamespace(
            argv=("/usr/bin/codex",), stdin="", cwd="/", env=None, timeout_sec=30.0
        ),
        parse=lambda **_kw: "",
        explain_failure=lambda **_kw: "",
    )
    real_init = CLIBackedLLMClient.__init__
    init_count = {"n": 0}

    def counting_init(self: Any, *args: Any, **kwargs: Any) -> None:
        init_count["n"] += 1
        return real_init(self, *args, **kwargs)

    with mock.patch.object(CLIBackedLLMClient, "__init__", counting_init):
        client = CLIBackedAgentClient(fake_adapter, model=None)
        with mock.patch.object(
            CLIBackedLLMClient, "invoke", return_value=LLMResponse(content="ok")
        ):
            client.invoke([{"role": "user", "content": "a"}])
            client.invoke([{"role": "user", "content": "b"}])

    assert init_count["n"] == 1


def test_cli_backed_agent_client_plain_text_response() -> None:
    """CLIBackedAgentClient treats non-JSON output as a final text answer."""
    import types as _types
    import unittest.mock as mock

    from app.services.agent_llm_client import CLIBackedAgentClient
    from app.services.llm_client import LLMResponse

    fake_adapter = _types.SimpleNamespace(
        name="codex",
        binary_env_key="CODEX_BIN",
        install_hint="",
        auth_hint="codex login",
        default_exec_timeout_sec=30.0,
        detect=lambda: _types.SimpleNamespace(
            installed=True, bin_path="/usr/bin/codex", logged_in=True, detail=""
        ),
        build=lambda **_kw: _types.SimpleNamespace(
            argv=("/usr/bin/codex",), stdin="", cwd="/", env=None, timeout_sec=30.0
        ),
        parse=lambda **_kw: "",
        explain_failure=lambda **_kw: "",
    )
    client = CLIBackedAgentClient(fake_adapter, model=None)

    with mock.patch(
        "app.integrations.llm_cli.runner.CLIBackedLLMClient.invoke",
        return_value=LLMResponse(content="The root cause is a memory leak."),
    ):
        result = client.invoke([{"role": "user", "content": "summarise"}])

    assert not result.has_tool_calls
    assert result.content == "The root cause is a memory leak."
