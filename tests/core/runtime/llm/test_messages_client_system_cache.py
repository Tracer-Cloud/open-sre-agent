"""The plain messages client must cache its system prefix.

The console showed the classification model (``claude-sonnet-4-6``) at a flat
0.0% cache-read ratio over 12.7M input tokens — a fifth of all input paying full
price. Cause: ``cache_control`` was only ever set by the agent client, while
classification and other single-shot calls go through ``LLMClient`` /
``BedrockLLMClient``, whose request kwargs carried no marker.

These calls resend a large stable system prompt (the 112-entry root-cause
taxonomy among others) with a small varying user message, which is the best case
for prefix caching.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from core.llm.transports.sdk.llm_clients import BedrockLLMClient, LLMClient

SYSTEM_PROMPT = "You classify incidents. " * 200
USER_MESSAGE = "classify this alert"


def _messages_client() -> LLMClient:
    client = LLMClient.__new__(LLMClient)
    client._model = "claude-sonnet-4-6"
    client._max_tokens = 512
    client._temperature = None
    client._api_key = "test-key"
    client._client = MagicMock()
    # _build_request_kwargs refreshes credentials; these tests only assert kwargs
    # shape, so skip env/keychain resolution (see test_cache_marker_degradation).
    client._ensure_client = lambda: None  # type: ignore[method-assign]
    return client


def _bedrock_client() -> BedrockLLMClient:
    client = BedrockLLMClient.__new__(BedrockLLMClient)
    client._model = "us.anthropic.claude-sonnet-4-6"
    client._max_tokens = 512
    client._temperature = None
    client._anthropic_client = MagicMock()
    return client


def _system_blocks(kwargs: dict[str, Any]) -> list[dict[str, Any]]:
    system = kwargs["system"]
    assert isinstance(system, list), f"system must be blocks to carry a marker: {system!r}"
    return system


def test_system_prompt_carries_a_cache_breakpoint() -> None:
    # Arrange
    client = _messages_client()

    # Act
    kwargs = client._build_request_kwargs(
        [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": USER_MESSAGE}]
    )

    # Assert
    (block,) = _system_blocks(kwargs)
    assert block["type"] == "text"
    assert block["text"] == SYSTEM_PROMPT
    assert block["cache_control"] == {"type": "ephemeral"}


def test_user_message_is_left_alone() -> None:
    """Only the stable prefix is marked; the varying tail must stay untouched."""
    # Arrange
    client = _messages_client()

    # Act
    kwargs = client._build_request_kwargs(
        [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": USER_MESSAGE}]
    )

    # Assert
    assert kwargs["messages"] == [{"role": "user", "content": USER_MESSAGE}]


def test_absent_system_prompt_adds_no_key() -> None:
    """A call with no system prompt must not gain an empty system block."""
    # Arrange
    client = _messages_client()

    # Act
    kwargs = client._build_request_kwargs([{"role": "user", "content": USER_MESSAGE}])

    # Assert
    assert "system" not in kwargs


def test_bedrock_path_caches_its_system_prompt_too() -> None:
    """Bedrock-hosted Claude bills the same way, so it needs the same marker.

    Bedrock builds its kwargs inline rather than through a shared helper, so
    this drives the real invoke and inspects what reached the SDK.
    """
    # Arrange
    client = _bedrock_client()
    client._anthropic_client.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="ok")],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=10, output_tokens=2),
    )

    # Act
    client._invoke_anthropic(
        [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": USER_MESSAGE}]
    )

    # Assert
    kwargs = client._anthropic_client.messages.create.call_args.kwargs
    (block,) = _system_blocks(kwargs)
    assert block["cache_control"] == {"type": "ephemeral"}
