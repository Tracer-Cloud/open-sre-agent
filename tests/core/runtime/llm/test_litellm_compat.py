from __future__ import annotations

import types
from typing import Any

from core.llm.litellm.clients import LiteLLMAgentClient, LiteLLMLLMClient


class _FakeMessage:
    def __init__(self, *, content: str, tool_calls: list[Any] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls or []

    def model_dump(self, *, exclude_none: bool = False) -> dict[str, Any]:
        _ = exclude_none
        return {"role": "assistant", "content": self.content, "tool_calls": self.tool_calls}


def _fake_response(*, content: str = "ok", tool_calls: list[Any] | None = None) -> Any:
    message = _FakeMessage(content=content, tool_calls=tool_calls)
    choice = types.SimpleNamespace(message=message, finish_reason="stop")
    usage = types.SimpleNamespace(prompt_tokens=11, completion_tokens=7)
    return types.SimpleNamespace(choices=[choice], usage=usage)


def test_litellm_agent_client_invokes_openai_compatible_endpoint() -> None:
    captured: dict[str, Any] = {}

    def completion(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return _fake_response(content="hello")

    client = LiteLLMAgentClient(
        litellm_model="openai/deepseek-v4-pro",
        max_tokens=123,
        api_base="https://api.deepseek.com",
        api_key_env="DEEPSEEK_API_KEY",
        credential_resolver=lambda _env: "ds-key",
        completion_func=completion,
    )

    response = client.invoke(
        [{"role": "user", "content": "hi"}],
        system="system prompt",
        tools=[{"type": "function", "function": {"name": "lookup", "parameters": {}}}],
    )

    assert response.content == "hello"
    assert captured["model"] == "openai/deepseek-v4-pro"
    assert captured["api_base"] == "https://api.deepseek.com"
    assert captured["api_key"] == "ds-key"
    assert captured["max_tokens"] == 123
    assert captured["tool_choice"] == "auto"
    assert captured["messages"][0] == {"role": "system", "content": "system prompt"}


def test_litellm_agent_client_parses_tool_calls() -> None:
    tool_call = types.SimpleNamespace(
        id="call_1",
        function=types.SimpleNamespace(name="lookup", arguments='{"service": "api"}'),
    )
    client = LiteLLMAgentClient(
        litellm_model="openai/deepseek-v4-pro",
        api_base="https://api.deepseek.com",
        api_key_env="DEEPSEEK_API_KEY",
        credential_resolver=lambda _env: "ds-key",
        completion_func=lambda **_kwargs: _fake_response(content="", tool_calls=[tool_call]),
    )

    response = client.invoke([{"role": "user", "content": "hi"}])

    assert response.stop_reason == "stop"
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].id == "call_1"
    assert response.tool_calls[0].name == "lookup"
    assert response.tool_calls[0].input == {"service": "api"}
    assert response.raw_content["tool_calls"] == [tool_call]


def test_litellm_llm_client_emits_usage_callback() -> None:
    usage: list[tuple[str, int | None, int | None]] = []
    client = LiteLLMLLMClient(
        litellm_model="openai/groq-model",
        api_base="https://api.groq.com/openai/v1",
        api_key_env="GROQ_API_KEY",
        credential_resolver=lambda _env: "groq-key",
        completion_func=lambda **_kwargs: _fake_response(content="done"),
        usage_callback=lambda model, tokens_in, tokens_out: usage.append(
            (model, tokens_in, tokens_out)
        ),
    )

    response = client.invoke("hi")

    assert response.content == "done"
    assert response.input_tokens == 11
    assert response.output_tokens == 7
    assert usage == [("openai/groq-model", 11, 7)]
