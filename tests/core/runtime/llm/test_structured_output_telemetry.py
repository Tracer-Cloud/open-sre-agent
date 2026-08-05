"""Native structured output must not bypass usage telemetry or retries.

``invoke()`` emits token usage (including prompt-cache read/write counts) and
retries transient provider errors with backoff. The native structured path added
alongside it called the SDK directly, so the diagnose stage — the call carrying
the large cached system prefix — vanished from token accounting, and a transient
429 fell straight through to the prompt-schema fallback instead of retrying.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from pydantic import BaseModel

from core.llm.transports.sdk.llm_clients import LLMClient, OpenAILLMClient


class _Payload(BaseModel):
    verdict: str


def _anthropic_client(monkeypatch: Any) -> tuple[LLMClient, MagicMock]:
    """Stub client. ``_ensure_client`` is neutralised: it re-resolves the real
    API key and would swap the mock for a live client (and bill for it)."""
    client = LLMClient.__new__(LLMClient)
    client._model = "claude-sonnet-4-6"
    client._max_tokens = 256
    client._temperature = None
    client._api_key = "test-key"
    sdk = MagicMock()
    client._client = sdk
    monkeypatch.setattr(client, "_ensure_client", lambda: None)
    return client, sdk


def _anthropic_response() -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text='{"verdict": "ok"}')],
        stop_reason="end_turn",
        usage=SimpleNamespace(
            input_tokens=11,
            output_tokens=3,
            cache_read_input_tokens=4096,
            cache_creation_input_tokens=0,
        ),
    )


def test_anthropic_structured_call_reports_usage(caplog: Any, monkeypatch: Any) -> None:
    """The cached prefix on this call must show up in token accounting."""
    # Arrange
    caplog.set_level(logging.DEBUG, logger="core.llm.shared.usage")
    client, sdk = _anthropic_client(monkeypatch)
    sdk.messages.create.return_value = _anthropic_response()

    # Act
    client.invoke_structured(_Payload, "diagnose this")

    # Assert
    line = next(record.message for record in caplog.records if "llm-usage" in record.message)
    assert "cache_read=4096" in line


def test_anthropic_structured_call_retries_transient_errors(monkeypatch: Any) -> None:
    """A transient failure must retry, not fall through to the slower fallback."""
    # Arrange
    client, sdk = _anthropic_client(monkeypatch)
    sdk.messages.create.side_effect = [RuntimeError("overloaded"), _anthropic_response()]

    # Act
    text = client.invoke_structured(_Payload, "diagnose this")

    # Assert
    assert sdk.messages.create.call_count == 2
    assert "ok" in text


def _openai_client() -> tuple[OpenAILLMClient, MagicMock]:
    client = OpenAILLMClient.__new__(OpenAILLMClient)
    client._model = "gpt-5.4-mini"
    client._max_tokens = 256
    client._temperature = None
    client._api_key_env = "OPENAI_API_KEY"
    client._api_key_default = "test-key"
    client._reasoning_effort = None
    sdk = MagicMock()
    client._client = sdk
    return client, sdk


def _openai_completion() -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(message=SimpleNamespace(refusal=None, parsed=_Payload(verdict="ok")))
        ],
        usage=SimpleNamespace(
            prompt_tokens=11,
            completion_tokens=3,
            prompt_tokens_details=SimpleNamespace(cached_tokens=2048),
        ),
    )


def test_openai_structured_call_reports_usage(caplog: Any, monkeypatch: Any) -> None:
    # Arrange
    caplog.set_level(logging.DEBUG, logger="core.llm.shared.usage")
    client, sdk = _openai_client()
    monkeypatch.setattr(client, "_ensure_client", lambda: sdk)
    sdk.chat.completions.parse.return_value = _openai_completion()

    # Act
    client.invoke_structured(_Payload, "diagnose this")

    # Assert
    line = next(record.message for record in caplog.records if "llm-usage" in record.message)
    assert "cache_read=2048" in line


def test_openai_structured_call_retries_transient_errors(monkeypatch: Any) -> None:
    # Arrange
    client, sdk = _openai_client()
    monkeypatch.setattr(client, "_ensure_client", lambda: sdk)
    sdk.chat.completions.parse.side_effect = [RuntimeError("overloaded"), _openai_completion()]

    # Act
    parsed = client.invoke_structured(_Payload, "diagnose this")

    # Assert
    assert sdk.chat.completions.parse.call_count == 2
    assert parsed.verdict == "ok"
