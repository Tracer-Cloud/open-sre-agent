from __future__ import annotations

import sys
import types

import pytest

from app.services.agent_llm_client import BedrockAgentClient


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
