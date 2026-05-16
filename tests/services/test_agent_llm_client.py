from __future__ import annotations

import sys
import types

import pytest

from app.services.agent_llm_client import BedrockAgentClient, OpenAIAgentClient


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

    class Anthropic:
        def __init__(self, **_: object) -> None:
            self.messages = types.SimpleNamespace(create=lambda **_: None)

    class AnthropicBedrock:
        def __init__(self, **_: object) -> None:
            self.messages = types.SimpleNamespace(create=lambda **_: None)

    fake_module.AuthenticationError = AuthenticationError
    fake_module.BadRequestError = BadRequestError
    fake_module.NotFoundError = NotFoundError
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
