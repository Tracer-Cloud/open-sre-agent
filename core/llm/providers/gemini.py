"""Gemini-specific request normalization for OpenAI-compatible transports."""

from __future__ import annotations

from typing import Any

from config.constants import GEMINI_API_KEY_ENV, GEMINI_THOUGHT_SIGNATURE_BYPASS


def signed_messages_extra_body(
    messages: list[dict[str, Any]], api_key_env: str, model: str
) -> dict[str, Any] | None:
    """Return Gemini replay messages with synthetic tool history marked safely."""
    if api_key_env != GEMINI_API_KEY_ENV:
        return None

    normalized_messages = messages
    needs_raw_messages = False
    for index, message in enumerate(messages):
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            continue
        needs_raw_messages = True
        first_call = tool_calls[0]
        if not isinstance(first_call, dict):
            continue
        extra_content = first_call.get("extra_content")
        google = extra_content.get("google") if isinstance(extra_content, dict) else None
        has_signature = isinstance(google, dict) and bool(google.get("thought_signature"))
        if has_signature or not model.casefold().startswith("gemini-3"):
            continue

        normalized_google = dict(google) if isinstance(google, dict) else {}
        normalized_google["thought_signature"] = GEMINI_THOUGHT_SIGNATURE_BYPASS
        normalized_extra_content = dict(extra_content) if isinstance(extra_content, dict) else {}
        normalized_extra_content["google"] = normalized_google
        normalized_first_call = dict(first_call)
        normalized_first_call["extra_content"] = normalized_extra_content
        normalized_tool_calls = list(tool_calls)
        normalized_tool_calls[0] = normalized_first_call
        normalized_message = dict(message)
        normalized_message["tool_calls"] = normalized_tool_calls
        if normalized_messages is messages:
            normalized_messages = list(messages)
        normalized_messages[index] = normalized_message

    return {"messages": normalized_messages} if needs_raw_messages else None
