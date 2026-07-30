"""Prompt-cache markers must never make a model unusable.

Caching is an optimisation. A model that rejects the markers — older Bedrock
model IDs are the realistic case — must still answer, uncached. Detection by
error wording alone is not enough: Bedrock's ``ValidationException`` text varies
and does not always name the field, so a request that only fails *because of* the
markers can look like an ordinary bad request.

The rule these tests pin: a 400 whose wording implicates the markers — including
Bedrock's generic ``ValidationException`` phrasings, which name no field — must
degrade to an uncached retry rather than failing the call. Bad requests that are
not about caching still raise, and leave caching enabled.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from core.llm.transports.sdk.llm_clients import BedrockLLMClient, LLMClient

SYSTEM_PROMPT = "You are a classifier. " * 200


def _bad_request(message: str) -> Exception:
    """An SDK-shaped 400 the client's except-clause will match."""
    from anthropic import BadRequestError

    response = SimpleNamespace(status_code=400, headers={}, request=SimpleNamespace())
    return BadRequestError(message, response=response, body=None)


def _ok_response() -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text="fine")],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=5, output_tokens=1),
    )


def _anthropic_client(monkeypatch: Any) -> tuple[LLMClient, MagicMock]:
    client = LLMClient.__new__(LLMClient)
    client._model = "claude-sonnet-4-6"
    client._max_tokens = 128
    client._temperature = None
    client._api_key = "test-key"
    sdk = MagicMock()
    client._client = sdk
    monkeypatch.setattr(client, "_ensure_client", lambda: None)
    return client, sdk


def _bedrock_client(monkeypatch: Any) -> tuple[BedrockLLMClient, MagicMock]:
    client = BedrockLLMClient.__new__(BedrockLLMClient)
    client._model = "us.anthropic.claude-3-5-sonnet-20240620-v1:0"
    client._max_tokens = 128
    client._temperature = None
    sdk = MagicMock()
    client._anthropic_client = sdk
    return client, sdk


def _messages() -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "classify"},
    ]


# Bedrock does not always name the offending field.
GENERIC_BEDROCK_REJECTION = "ValidationException: This model does not support the requested feature"


def test_generic_rejection_still_falls_back_to_uncached(monkeypatch: Any) -> None:
    """The wording names no field, so only a retry can tell markers were at fault."""
    # Arrange: markers rejected, stripped request accepted.
    client, sdk = _anthropic_client(monkeypatch)
    sdk.messages.create.side_effect = [_bad_request(GENERIC_BEDROCK_REJECTION), _ok_response()]

    # Act
    response = client.invoke(_messages())

    # Assert: answered anyway, and the retry carried no cache markers.
    assert response.content == "fine"
    retried = sdk.messages.create.call_args_list[-1].kwargs
    assert "cache_control" not in json.dumps(retried["system"])


def test_markers_stay_off_after_a_rejection(monkeypatch: Any) -> None:
    """One probe per client, not one per call."""
    # Arrange
    client, sdk = _anthropic_client(monkeypatch)
    sdk.messages.create.side_effect = [
        _bad_request(GENERIC_BEDROCK_REJECTION),
        _ok_response(),
        _ok_response(),
    ]

    # Act
    client.invoke(_messages())
    sdk.messages.create.reset_mock()
    client.invoke(_messages())

    # Assert: the second call never re-sends markers.
    sent = sdk.messages.create.call_args.kwargs
    assert "cache_control" not in json.dumps(sent["system"])
    assert sdk.messages.create.call_count == 1


def test_unrelated_bad_request_is_not_masked(monkeypatch: Any) -> None:
    """A genuine 400 must surface, not be reported as a caching problem."""
    # Arrange: both attempts fail — the request itself is bad.
    client, sdk = _anthropic_client(monkeypatch)
    sdk.messages.create.side_effect = [
        _bad_request("max_tokens: must be <= 8192"),
        _bad_request("max_tokens: must be <= 8192"),
    ]

    # Act / Assert
    with pytest.raises(RuntimeError, match="max_tokens"):
        client.invoke(_messages())


def test_unrelated_bad_request_leaves_caching_on(monkeypatch: Any) -> None:
    """A bad request that is not about markers must not disable caching."""
    # Arrange
    client, sdk = _anthropic_client(monkeypatch)
    sdk.messages.create.side_effect = [
        _bad_request("max_tokens: must be <= 8192"),
        _bad_request("max_tokens: must be <= 8192"),
    ]

    # Act
    with pytest.raises(RuntimeError):
        client.invoke(_messages())

    # Assert
    assert client._cache_markers_enabled is True


def test_bedrock_client_falls_back_the_same_way(monkeypatch: Any) -> None:
    """Bedrock is the realistic home of marker-rejecting model IDs."""
    # Arrange
    client, sdk = _bedrock_client(monkeypatch)
    sdk.messages.create.side_effect = [_bad_request(GENERIC_BEDROCK_REJECTION), _ok_response()]
    monkeypatch.setattr(client, "_ensure_client", lambda: None, raising=False)

    # Act
    response = client._invoke_anthropic(_messages())

    # Assert
    assert response.content == "fine"
    retried = sdk.messages.create.call_args_list[-1].kwargs
    assert "cache_control" not in json.dumps(retried["system"])


def test_concurrent_rejection_still_retries_uncached(monkeypatch: Any) -> None:
    """A second in-flight marked request must not be stranded by the first.

    Clients are shared across concurrent turns. If one call's rejection flips
    the shared flag before another call handles its own rejection, the second
    request — already sent *with* markers — would see the flag off, skip its
    retry, and fail. The retry decision must depend on what this request
    carried, not on the shared flag's current value.
    """
    # Arrange: the SDK flips the flag before raising, as a racing call would.
    client, sdk = _anthropic_client(monkeypatch)
    calls: list[dict[str, Any]] = []

    def _create(**kwargs: Any) -> Any:
        calls.append(kwargs)
        if "cache_control" in json.dumps(kwargs.get("system", "")):
            client._cache_markers_enabled = False  # another turn got there first
            raise _bad_request(GENERIC_BEDROCK_REJECTION)
        return _ok_response()

    sdk.messages.create.side_effect = _create

    # Act
    response = client.invoke(_messages())

    # Assert: it still recovered rather than surfacing the 400.
    assert response.content == "fine"
    assert "cache_control" not in json.dumps(calls[-1].get("system", ""))
